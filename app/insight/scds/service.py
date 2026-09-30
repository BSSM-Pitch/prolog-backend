"""SCDS (Ring 4) — 설정 충돌 감지.

**어디서 무엇을 부르나(Ring).** 사건(`insight.events`)은 SCDS 소유다. 경로가 `/chapters/{id}/events`
라 MSU 처럼 보이지만, 사건 저장 · 룰 검출은 여기서 한다. 룰 검출에 필요한 것 — 챕터(MSU, Ring 2),
확정 캐릭터(ASS, Ring 3), 세계관 규칙(REX, Ring 3) — 은 전부 **아래** Ring 이라 직접 읽는다.
위 Ring 을 부르지 않고, 같은 Ring 4(FTS · RCV)도 부르지 않는다. 그래서 이벤트가 필요 없다.

**흐름(명세 §5).**
1. 사건 저장 요청 안에서 `run_scds_rules`(LLM 없음, 동기). 억제된 후보(§4.11 `ignored` 와
   같은 조합)는 백엔드가 거른다 — 패키지는 억제를 모른다.
2. 후보가 없으면 검사는 `skipped`. 있으면 `conflict_check` 잡(ai 큐) → `run_scds_analysis`.
3. AI 가 성공하면 AI 가 남긴 충돌을 만든다(`detected_by = ai`). 끝내 실패하면 룰 후보를 조언 없는
   충돌로 만든다(`detected_by = rule`) — 명세 §4.7 "failed 면 후보만 표시", ERD "advice null".
"""

import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.client import AIClient, Result
from app.authoring.ass.models import Character, CharacterAttribute
from app.authoring.rex.models import WorldRule
from app.content.manuscripts.models import Chapter
from app.core import errors
from app.insight.scds.models import Conflict, ConflictSuppression, Event, EventCharacter
from app.insight.scds.schemas import (
    CheckStatus,
    ConflictAction,
    ConflictCheckResponse,
    ConflictResponse,
    EventCreate,
    EventCreated,
    EventResponse,
    HistoryItem,
    RuleResult,
)
from app.jobs import service as jobs
from app.jobs.models import Job

JOB_TYPE = "conflict_check"  # 0001 jobs_job_type_chk 의 이름
QUEUE = "ai"
Cursor = tuple[datetime, UUID] | None


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def suppression_key(character_id: str, rule_id: str, content: str) -> str:
    """ERD: sha256(character_id : rule_id : normalize(event_description))."""
    return hashlib.sha256(f"{character_id}:{rule_id}:{normalize(content)}".encode()).hexdigest()


# --- 룰 검출 입력 ---------------------------------------------------------------


async def _chapter(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> Chapter:
    stmt = select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
    chapter = (await session.execute(stmt)).scalar_one_or_none()
    if chapter is None:
        raise errors.ChapterNotFound()
    return chapter


async def characters_payload(
    session: AsyncSession, project_id: UUID, ids: Sequence[UUID]
) -> list[dict[str, Any]]:
    """ASS 확정 캐릭터를 패키지가 받는 모양(ASS 명세 §2.4 ConfirmedCharacter)으로.

    패키지는 문자열 배열과 `influence_relations: [{target}]` 를 받는다.
    필드 이름은 §12 결정대로 ASS 이름이다.
    """
    rows = list(
        (
            await session.execute(
                select(Character).where(
                    Character.id.in_(ids),
                    Character.project_id == project_id,
                    Character.status == "active",
                )
            )
        ).scalars()
    )
    if len(rows) != len(set(ids)):
        missing = sorted(str(i) for i in set(ids) - {c.id for c in rows})
        raise errors.CharacterNotFound(character_ids=missing)
    attrs = (
        await session.execute(
            select(CharacterAttribute)
            .where(CharacterAttribute.character_id.in_(ids))
            .order_by(CharacterAttribute.created_at)
        )
    ).scalars()
    by: dict[UUID, dict[str, list[str]]] = {c.id: {} for c in rows}
    for a in attrs:
        by[a.character_id].setdefault(a.category, []).append(a.value)
    return [
        {
            "character_id": str(c.id),
            "name": c.name,
            "personality_tags": by[c.id].get("personality_tag", []),
            "core_values": by[c.id].get("core_value", []),
            "influence_relations": [{"target": v} for v in by[c.id].get("influence_relation", [])],
            "emotion_keywords": by[c.id].get("emotion_keyword", []),
        }
        for c in rows
    ]


async def rules_payload(session: AsyncSession, project_id: UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(select(WorldRule).where(WorldRule.project_id == project_id))
    ).scalars()
    return [
        {
            "rule_id": str(r.id),
            "description": r.description,
            "violation_keywords": list(r.violation_keywords),
        }
        for r in rows
    ]


async def _suppressed(session: AsyncSession, project_id: UUID, keys: Sequence[str]) -> set[str]:
    if not keys:
        return set()
    stmt = select(ConflictSuppression.suppression_key).where(
        ConflictSuppression.project_id == project_id,
        ConflictSuppression.suppression_key.in_(keys),
    )
    return set((await session.execute(stmt)).scalars())


# --- 응답 ---------------------------------------------------------------------


def status_of(job: Job) -> CheckStatus:
    return cast(CheckStatus, {"running": "analyzing"}.get(job.status, job.status))


def _rule_result(job: Job) -> dict[str, Any]:
    return dict(job.input.get("rule_result") or {})


async def to_check(session: AsyncSession, job: Job) -> ConflictCheckResponse:
    ids = (
        await session.execute(
            select(Conflict.id).where(Conflict.job_id == job.id).order_by(Conflict.created_at)
        )
    ).scalars()
    return ConflictCheckResponse(
        check_id=job.id,
        event_id=cast(UUID, job.target_id),
        status=status_of(job),
        rule_result=RuleResult.model_validate(_rule_result(job)),
        conflict_ids=list(ids),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def check_error(job: Job) -> dict[str, Any] | None:
    if job.status != "failed" or not job.error:
        return None
    details = {k: v for k, v in (job.error.get("details") or {}).items() if k != "rule_result"}
    return {
        "code": job.error.get("code", "AI_ANALYSIS_FAILED"),
        "message": job.error.get("message", "AI 분석에 실패했습니다."),
        "details": details,
    }


async def _event_response(session: AsyncSession, event: Event) -> EventResponse:
    chapter_no = (
        await session.execute(select(Chapter.chapter_no).where(Chapter.id == event.chapter_id))
    ).scalar_one()
    ids = (
        await session.execute(
            select(EventCharacter.character_id).where(EventCharacter.event_id == event.id)
        )
    ).scalars()
    return EventResponse(
        event_id=event.id,
        chapter_id=event.chapter_id,
        chapter=chapter_no,
        character_ids=list(ids),
        content=event.description,
        created_at=event.created_at,
    )


async def to_conflicts(session: AsyncSession, rows: Sequence[Conflict]) -> list[ConflictResponse]:
    events: dict[UUID, Event] = {}
    numbers: dict[UUID, int] = {}
    if rows:
        found = await session.execute(select(Event).where(Event.id.in_([c.event_id for c in rows])))
        events = {e.id: e for e in found.scalars()}
        chapter_ids = {c.chapter_id for c in rows if c.chapter_id}
        if chapter_ids:
            pairs = await session.execute(
                select(Chapter.id, Chapter.chapter_no).where(Chapter.id.in_(chapter_ids))
            )
            numbers = {i: n for i, n in pairs}
    return [
        ConflictResponse(
            conflict_id=c.id,
            check_id=c.job_id,
            event_id=c.event_id,
            character_id=c.character_id,
            rule_id=c.rule_id,
            chapter_id=c.chapter_id,
            chapter=numbers.get(c.chapter_id) if c.chapter_id else None,
            conflict_target=c.conflict_target,
            matched_keyword=c.matched_keyword,
            input_event=events[c.event_id].description if c.event_id in events else "",
            detected_by=cast(Any, c.detected_by),
            severity=cast(Any, c.severity),
            advice=c.advice,
            status=cast(Any, c.status),
            modified_content=c.modified_content,
            created_at=c.created_at,
            resolved_at=c.resolved_at,
        )
        for c in rows
    ]


# --- 사건 저장 + 룰 검출(명세 §4.6) -------------------------------------------------


async def create_event(
    session: AsyncSession,
    project_id: UUID,
    chapter_id: UUID,
    body: EventCreate,
    user_id: UUID,
    ai: AIClient,
) -> EventCreated:
    chapter = await _chapter(session, project_id, chapter_id)
    ids = list(dict.fromkeys(body.character_ids))
    characters = await characters_payload(session, project_id, ids)
    event = Event(
        project_id=project_id, chapter_id=chapter.id, description=body.content, created_by=user_id
    )
    session.add(event)
    await session.flush()
    session.add_all(
        EventCharacter(event_id=event.id, character_id=i, project_id=project_id) for i in ids
    )

    payload = {"character_ids": [str(i) for i in ids], "content": body.content}
    result = ai.run_scds_rules(payload, await rules_payload(session, project_id), characters)
    if "error" in result:
        raise errors.RuleEngineError(detail=result["error"].get("message"))
    rule_result = dict(result["data"]["rule_result"])

    keys = {
        suppression_key(c["character_id"], c["rule_id"], body.content): c
        for c in rule_result["candidates"]
    }
    blocked = await _suppressed(session, project_id, list(keys))
    rule_result["candidates"] = [c for k, c in keys.items() if k not in blocked]
    rule_result["suppressed_count"] = len(blocked)
    rule_result["has_candidate"] = bool(rule_result["candidates"])

    job = await jobs.create(
        session,
        project_id=project_id,
        job_type=JOB_TYPE,
        target_type="event",
        target_id=event.id,
        queue=QUEUE,
        input={"rule_result": rule_result},
        created_by=user_id,
    )
    if rule_result["has_candidate"]:
        jobs.announce(session, job)
    else:
        # 참조 데이터가 없거나 후보가 없다 — AI 를 부르지 않는다(명세 §4.6 "후보 없음").
        job.status, job.result = "skipped", {"rule_result": rule_result}
    await session.flush()
    await session.refresh(job)
    await session.refresh(event)
    return EventCreated(
        event=await _event_response(session, event), conflict_check=await to_check(session, job)
    )


# --- 검사 작업 ------------------------------------------------------------------


async def _check(
    session: AsyncSession, project_id: UUID, check_id: UUID, *, lock: bool = False
) -> Job:
    stmt = select(Job).where(
        Job.id == check_id, Job.project_id == project_id, Job.job_type == JOB_TYPE
    )
    if lock:
        stmt = stmt.with_for_update()
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise errors.ConflictCheckNotFound()
    return job


async def get_check(session: AsyncSession, project_id: UUID, check_id: UUID) -> Job:
    return await _check(session, project_id, check_id)


async def retry_check(session: AsyncSession, project_id: UUID, check_id: UUID) -> Job:
    """`failed` 에서만. 저장한 rule_result 로 AI 분석만 다시 한다(패키지 README).

    끝내 실패해 룰 후보로 만든 충돌은 지운다 — 다시 분석하면 AI 가 새로 만든다.
    """
    job = await _check(session, project_id, check_id, lock=True)
    if job.status != "failed":
        raise errors.InvalidStatusTransition(status=status_of(job))
    stale = (
        await session.execute(
            select(Conflict).where(Conflict.job_id == job.id, Conflict.status == "pending")
        )
    ).scalars()
    for c in stale:
        await session.delete(c)
    retried = await jobs.retry(session, job.id, by_user=True)
    assert retried is not None
    return retried


# --- 워커(명세 §4.7 AI 분석) -------------------------------------------------------


async def _prepare(session: AsyncSession, job: Job) -> dict[str, Any]:
    event = await session.get(Event, cast(UUID, job.target_id))
    if event is None:
        raise errors.EventNotFound()
    ids = list(
        (
            await session.execute(
                select(EventCharacter.character_id).where(EventCharacter.event_id == event.id)
            )
        ).scalars()
    )
    rule_result = _rule_result(job)
    rule_result.pop("suppressed_count", None)  # 패키지 RuleResult 에 없는 필드
    return {
        "event": {"character_ids": [str(i) for i in ids], "content": event.description},
        "rule_result": rule_result,
        "characters": await characters_payload(session, job.project_id, ids),
    }


def _call(ai: AIClient, args: Any) -> Result:
    return ai.run_scds_analysis(args["event"], args["rule_result"], args["characters"])


async def _conflict_rows(
    session: AsyncSession, job: Job, found: list[dict[str, Any]], detected_by: str
) -> None:
    event = await session.get(Event, cast(UUID, job.target_id))
    if event is None:
        return
    by_pair = {
        (c["character_id"], c["conflict_target"]): c for c in _rule_result(job)["candidates"]
    }
    for f in found:
        candidate = by_pair.get((f["character_id"], f["conflict_target"]))
        if candidate is None:
            continue
        session.add(
            Conflict(
                project_id=job.project_id,
                event_id=event.id,
                chapter_id=event.chapter_id,
                character_id=UUID(candidate["character_id"]),
                rule_id=UUID(candidate["rule_id"]),
                detected_by=detected_by,
                severity=f.get("severity"),
                advice=f.get("advice"),
                conflict_target=candidate["conflict_target"],
                matched_keyword=candidate["matched_keyword"],
                suppression_key=suppression_key(
                    candidate["character_id"], candidate["rule_id"], event.description
                ),
                job_id=job.id,
            )
        )


async def _analyzed(session: AsyncSession, job: Job, data: dict[str, Any]) -> None:
    await _conflict_rows(session, job, list(data.get("conflicts") or []), "ai")


async def mark_failed(session: AsyncSession, job: Job, error: dict[str, Any] | None = None) -> None:
    """AI 가 끝내 실패하면 룰 후보를 조언 없는 충돌로 남긴다. 스위퍼(좀비 소진)도 부른다."""
    await _conflict_rows(session, job, list(_rule_result(job).get("candidates") or []), "rule")


async def handle(sessions: Any, job_id: UUID, ai: AIClient) -> ai_jobs.Outcome:
    return await ai_jobs.run(
        sessions,
        job_id,
        JOB_TYPE,
        ai,
        prepare=_prepare,
        call=_call,
        on_success=_analyzed,
        on_failure=mark_failed,
    )


# --- 충돌 조회 · 처리(명세 §4.9~4.12) -------------------------------------------------


async def list_conflicts(
    session: AsyncSession,
    project_id: UUID,
    limit: int,
    cursor: Cursor,
    *,
    status: str | None = None,
    chapter_id: UUID | None = None,
    character_id: UUID | None = None,
    severity: str | None = None,
) -> list[Conflict]:
    stmt = select(Conflict).where(Conflict.project_id == project_id)
    for column, value in (
        (Conflict.status, status),
        (Conflict.chapter_id, chapter_id),
        (Conflict.character_id, character_id),
        (Conflict.severity, severity),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    stmt = stmt.order_by(Conflict.created_at.desc(), Conflict.id.desc()).limit(limit + 1)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Conflict.created_at, Conflict.id)
            < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def _conflict(
    session: AsyncSession, project_id: UUID, conflict_id: UUID, *, lock: bool = False
) -> Conflict:
    stmt = select(Conflict).where(Conflict.id == conflict_id, Conflict.project_id == project_id)
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise errors.ConflictNotFound()
    return row


async def get_conflict(
    session: AsyncSession, project_id: UUID, conflict_id: UUID
) -> ConflictResponse:
    return (await to_conflicts(session, [await _conflict(session, project_id, conflict_id)]))[0]


async def resolve(
    session: AsyncSession, project_id: UUID, conflict_id: UUID, body: ConflictAction, user_id: UUID
) -> ConflictResponse:
    """수용 · 무시 · 수정. 한 번만 처리한다(아니면 `INVALID_STATUS_TRANSITION`).

    `ignored` 는 억제 키를 남긴다 — 같은 캐릭터 · 규칙 · 사건 문장은 다시 후보가 되지 않는다(§4.11).
    """
    row = await _conflict(session, project_id, conflict_id, lock=True)
    if row.status != "pending":
        raise errors.InvalidStatusTransition(status=row.status)
    if body.action == "modified" and not body.modified_content:
        raise errors.InvalidInput(
            "modified 에는 modified_content 가 필요합니다", field="modified_content"
        )
    row.status = body.action
    row.modified_content = body.modified_content if body.action == "modified" else None
    row.resolved_at = datetime.now(UTC)
    if body.action == "ignored":
        await session.execute(
            insert(ConflictSuppression)
            .values(project_id=project_id, suppression_key=row.suppression_key, created_by=user_id)
            .on_conflict_do_nothing()
        )
    await session.flush()
    await session.refresh(row)
    return (await to_conflicts(session, [row]))[0]


async def history(
    session: AsyncSession,
    project_id: UUID,
    limit: int,
    cursor: Cursor,
    *,
    character_id: UUID | None = None,
    chapter_from: int | None = None,
    chapter_to: int | None = None,
) -> list[Conflict]:
    """처리한 충돌(명세 §4.12, 선택 기능). 최근 처리부터."""
    stmt = (
        select(Conflict)
        .outerjoin(Chapter, Chapter.id == Conflict.chapter_id)
        .where(Conflict.project_id == project_id, Conflict.status != "pending")
    )
    if character_id is not None:
        stmt = stmt.where(Conflict.character_id == character_id)
    if chapter_from is not None:
        stmt = stmt.where(Chapter.chapter_no >= chapter_from)
    if chapter_to is not None:
        stmt = stmt.where(Chapter.chapter_no <= chapter_to)
    stmt = stmt.order_by(Conflict.created_at.desc(), Conflict.id.desc()).limit(limit + 1)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Conflict.created_at, Conflict.id)
            < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


def to_history(rows: Sequence[Conflict]) -> list[HistoryItem]:
    return [
        HistoryItem(conflict_id=c.id, status=cast(Any, c.status), resolved_at=c.resolved_at)
        for c in rows
    ]
