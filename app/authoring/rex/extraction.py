"""REX AI 추출 — 원고 본문에서 세계관 규칙 후보를 뽑고, 사용자가 골라 확정한다(명세 §4.1~4.4).

추출은 `ops.jobs` 한 행(`rule_extraction`, ai 큐)이고 `extraction_id` 가 잡 id 다. 후보는 잡
`result.extracted_rules` 에 남고, **확정해야** `world_rules` 행(`origin = ai_extracted`)이 된다 —
명세 §4.4 "추출 결과 중 선택 항목을 WorldRule 로 확정 저장". 후보마다 검토 상태를
`result.reviews[index]` 에 둔다(화면 21 "검토 대기 · 확정 · 무시").

원고는 REX 가 MSU(Ring 2)에서 읽는다 — 아래 Ring 이라 import 할 수 있다.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.client import AIClient, Result
from app.authoring.rex.models import WorldRule
from app.authoring.rex.schemas import (
    CandidateEdit,
    ExtractionStatus,
    RuleCandidate,
    RuleExtractionConfirm,
    RuleExtractionResponse,
)
from app.content.manuscripts import service as manuscripts
from app.core import errors
from app.jobs import service as jobs
from app.jobs.models import Job

JOB_TYPE = "rule_extraction"  # 0001 jobs_job_type_chk 의 이름
QUEUE = "ai"
_NO_EDIT = CandidateEdit()


def status_of(job: Job) -> ExtractionStatus:
    return cast(
        ExtractionStatus,
        {"running": "extracting", "completed": "completed", "failed": "failed"}.get(
            job.status, "queued"
        ),
    )


def _rules(job: Job) -> list[dict[str, Any]]:
    return list((job.result or {}).get("extracted_rules") or [])


def _reviews(job: Job) -> dict[str, dict[str, Any]]:
    return dict((job.result or {}).get("reviews") or {})


def to_response(job: Job) -> RuleExtractionResponse:
    reviews = _reviews(job)
    return RuleExtractionResponse(
        extraction_id=job.id,
        manuscript_id=cast(UUID, job.target_id),
        status=status_of(job),
        extracted_rules=[
            RuleCandidate(
                index=i,
                title=r["title"],
                description=r["description"],
                violation_keywords=list(r.get("violation_keywords") or []),
                evidence=r.get("evidence"),
                source_chapter=r.get("source_chapter"),
                review_status=reviews.get(str(i), {}).get("status", "pending"),
                rule_id=reviews.get(str(i), {}).get("rule_id"),
            )
            for i, r in enumerate(_rules(job))
        ],
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def error_of(job: Job) -> dict[str, Any] | None:
    if job.status != "failed" or not job.error:
        return None
    return {
        "code": job.error.get("code", "AI_EXTRACTION_FAILED"),
        "message": job.error.get("message", "AI 추출 처리 중 오류가 발생했습니다."),
        "details": job.error.get("details") or {},
    }


async def _job(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    extraction_id: UUID,
    *,
    lock: bool = False,
) -> Job:
    stmt = select(Job).where(
        Job.id == extraction_id,
        Job.project_id == project_id,
        Job.job_type == JOB_TYPE,
        Job.target_id == manuscript_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise errors.RuleExtractionNotFound()
    return job


async def request(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, user_id: UUID
) -> Job:
    """본문이 준비된 원고만 된다. 업로드 추출 중이거나 빈 원고면 `INVALID_INPUT`."""
    manuscript = await manuscripts.get(session, project_id, manuscript_id)  # 없으면 404
    if manuscript.status != "ready" or not (manuscript.content or "").strip():
        raise errors.InvalidInput(
            "본문이 준비된 원고만 규칙을 추출할 수 있습니다",
            field="manuscript_id",
            status=manuscript.status,
        )
    job = await jobs.create(
        session,
        project_id=project_id,
        job_type=JOB_TYPE,
        target_type="manuscript",
        target_id=manuscript_id,
        queue=QUEUE,
        created_by=user_id,
    )
    jobs.announce(session, job)
    await session.flush()
    await session.refresh(job)
    return job


async def get(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, extraction_id: UUID
) -> Job:
    return await _job(session, project_id, manuscript_id, extraction_id)


async def retry(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, extraction_id: UUID
) -> Job:
    """`failed` 에서만. 완료본을 다시 뽑으려면 새 추출을 요청한다(명세 §4.3 비고)."""
    job = await _job(session, project_id, manuscript_id, extraction_id, lock=True)
    if job.status != "failed":
        raise errors.ExtractionNotReady(status=status_of(job))
    retried = await jobs.retry(session, job.id, by_user=True)
    assert retried is not None
    return retried


async def confirm(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    extraction_id: UUID,
    body: RuleExtractionConfirm,
) -> list[WorldRule]:
    """고른 후보를 규칙으로 만든다. 이미 확정한 후보는 건너뛴다(두 번 눌러도 규칙은 하나).

    `edits` 는 확정 전에 고친 값이다 — 원본 후보는 잡에 그대로 남는다. `ignored_indices` 는
    검토 상태만 바꾼다(화면 21 "무시"). 무시한 후보도 나중에 확정할 수 있다.
    """
    job = await _job(session, project_id, manuscript_id, extraction_id, lock=True)
    if job.status != "completed":
        raise errors.ExtractionNotReady(status=status_of(job))
    rules, reviews = _rules(job), _reviews(job)
    selected, ignored = list(dict.fromkeys(body.selected_indices)), set(body.ignored_indices)
    wrong = [i for i in [*selected, *ignored] if not 0 <= i < len(rules)]
    edit_keys = {k for k in body.edits if k not in {str(i) for i in selected}}
    if wrong or edit_keys or set(selected) & ignored:
        raise errors.InvalidInput(
            "선택·무시·수정할 후보가 올바르지 않습니다",
            out_of_range=wrong,
            edits_not_selected=sorted(edit_keys),
            both=sorted(set(selected) & ignored),
        )

    created = []
    for i in selected:
        if reviews.get(str(i), {}).get("status") == "confirmed":
            continue
        candidate = rules[i] | body.edits.get(str(i), _NO_EDIT).model_dump(exclude_none=True)
        rule = WorldRule(
            project_id=project_id,
            title=candidate["title"],
            description=candidate["description"],
            violation_keywords=candidate.get("violation_keywords") or [],
            origin="ai_extracted",
            evidence=candidate.get("evidence"),
            extraction_job_id=job.id,
            source_chapter_no=candidate.get("source_chapter"),
        )
        session.add(rule)
        await session.flush()
        reviews[str(i)] = {"status": "confirmed", "rule_id": str(rule.id)}
        created.append(rule)
    for i in ignored:
        if reviews.get(str(i), {}).get("status") != "confirmed":
            reviews[str(i)] = {"status": "ignored", "rule_id": None}
    job.result = {**(job.result or {}), "reviews": reviews}
    await session.flush()
    for rule in created:
        await session.refresh(rule)
    return created


# --- 워커 --------------------------------------------------------------------


async def _prepare(session: AsyncSession, job: Job) -> str:
    """실행 시점의 본문을 읽는다. 원고가 그 사이 지워졌으면 재시도 없이 실패한다."""
    manuscript = await manuscripts.get(session, job.project_id, cast(UUID, job.target_id))
    return manuscript.content or ""


def _call(ai: AIClient, manuscript_text: Any) -> Result:
    return ai.run_rex(manuscript_text)


async def handle(sessions: Any, job_id: UUID, ai: AIClient) -> ai_jobs.Outcome:
    return await ai_jobs.run(sessions, job_id, JOB_TYPE, ai, prepare=_prepare, call=_call)
