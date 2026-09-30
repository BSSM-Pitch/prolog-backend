"""FTS (Ring 4) — 복선의 설치·연결·회수를 사용자가 기록한다. AI 호출은 없다(명세 머리말).

- 챕터 연결은 `foreshadowing_chapters` 한 곳이다(ERD "챕터 연결 일원화"). 번호는 `content.chapters`
  에서 조인해 읽는다 — Ring 4 는 Ring 2 를 읽을 수 있다. 캐시를 두지 않는 이유는 0006 머리말.
- `status` 는 챕터 행에서 도출한다(`_restatus`): 설치가 없으면 `orphaned`, 회수가 있으면 `resolved`,
  아니면 `unresolved`. 상태를 따로 고치는 경로가 없으니 행과 어긋날 수 없다.
- 바꾸는 경로는 전부 복선 행을 잠근다 — 회수를 두 번 동시에 지정해도 부분 UNIQUE 에 걸리지 않게.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, exists, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.authoring.ass.models import Character
from app.content.manuscripts.models import Chapter
from app.core import errors
from app.insight.fts.models import (
    Foreshadowing,
    ForeshadowingChapter,
    ForeshadowingCharacter,
    ForeshadowingEvent,
)
from app.insight.fts.schemas import (
    Advisory,
    ChapterForeshadowing,
    ChapterRef,
    ForeshadowingCreate,
    ForeshadowingResponse,
    ForeshadowingUpdate,
    Marker,
    Priority,
    SimilarCandidate,
    Status,
    StatusCounts,
    TimelineTrack,
    UnresolvedItem,
    UnresolvedResponse,
)

FC = ForeshadowingChapter
# 명세 §4.11 비고의 초기값: 10 미만 low · 10~25 medium · 25 초과 high.
MEDIUM_FROM, HIGH_OVER = 10, 25
Cursor = tuple[datetime, UUID] | None


def priority_of(elapsed: int) -> Priority:
    if elapsed > HIGH_OVER:
        return "high"
    return "medium" if elapsed >= MEDIUM_FROM else "low"


def advisory_message(setup_chapter: int, title: str) -> str:
    """결정론적 템플릿(명세 §4.11). 문구는 화면 24 를 따른다."""
    return (
        f"{setup_chapter}장에 설치한 '{title}' 복선이 아직 회수되지 않았어요. "
        "지금 흐름에서 다시 언급하거나 회수를 고려해 보세요."
    )


# --- 조회 헬퍼 -------------------------------------------------------------


async def _get(
    session: AsyncSession, project_id: UUID, foreshadowing_id: UUID, *, lock: bool = False
) -> Foreshadowing:
    stmt = select(Foreshadowing).where(
        Foreshadowing.id == foreshadowing_id, Foreshadowing.project_id == project_id
    )
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise errors.ForeshadowingNotFound()
    return row


async def _chapter(
    session: AsyncSession, project_id: UUID, chapter_id: UUID, field: str
) -> Chapter:
    """쓰기 시점의 존재·테넌시 검사. FK 가 없으므로(0006) 여기가 유일한 방어선이다."""
    stmt = select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
    chapter = (await session.execute(stmt)).scalar_one_or_none()
    if chapter is None:
        raise errors.InvalidInput("이 프로젝트의 챕터가 아닙니다", field=field)
    return chapter


async def _refs(session: AsyncSession, ids: Sequence[UUID]) -> dict[UUID, list[ChapterRef]]:
    out: dict[UUID, list[ChapterRef]] = {i: [] for i in ids}
    if not ids:
        return out
    stmt = (
        select(FC.foreshadowing_id, FC.chapter_id, FC.role, Chapter.chapter_no)
        .outerjoin(Chapter, Chapter.id == FC.chapter_id)
        .where(FC.foreshadowing_id.in_(ids))
        .order_by(Chapter.chapter_no.nulls_last(), FC.created_at)
    )
    for fid, chapter_id, role, chapter_no in await session.execute(stmt):
        out[fid].append(ChapterRef(chapter_id=chapter_id, chapter_no=chapter_no, role=role))
    return out


async def _links(
    session: AsyncSession, model: Any, column: Any, ids: Sequence[UUID]
) -> dict[UUID, list[UUID]]:
    out: dict[UUID, list[UUID]] = {i: [] for i in ids}
    if ids:
        stmt = select(model.foreshadowing_id, column).where(model.foreshadowing_id.in_(ids))
        for fid, target in await session.execute(stmt.order_by(model.created_at)):
            out[fid].append(target)
    return out


def _no(refs: list[ChapterRef], role: str) -> int | None:
    return next((r.chapter_no for r in refs if r.role == role and r.chapter_no is not None), None)


async def to_responses(
    session: AsyncSession, rows: Sequence[Foreshadowing]
) -> list[ForeshadowingResponse]:
    ids = [f.id for f in rows]
    refs = await _refs(session, ids)
    chars = await _links(session, ForeshadowingCharacter, ForeshadowingCharacter.character_id, ids)
    events = await _links(session, ForeshadowingEvent, ForeshadowingEvent.event_id, ids)
    return [
        ForeshadowingResponse(
            foreshadowing_id=f.id,
            project_id=f.project_id,
            title=f.title,
            description=f.description,
            setup_chapter=_no(refs[f.id], "setup"),
            linked_chapters=sorted(
                r.chapter_no for r in refs[f.id] if r.role == "linked" and r.chapter_no is not None
            ),
            payoff_chapter=_no(refs[f.id], "payoff"),
            status=f.status,  # type: ignore[arg-type]
            linked_event_ids=events[f.id],
            linked_character_ids=chars[f.id],
            chapters=refs[f.id],
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in rows
    ]


async def _one(session: AsyncSession, row: Foreshadowing) -> ForeshadowingResponse:
    await session.flush()
    await session.refresh(row)
    return (await to_responses(session, [row]))[0]


async def _role_no(session: AsyncSession, foreshadowing_id: UUID, role: str) -> int | None:
    stmt = (
        select(Chapter.chapter_no)
        .join(FC, FC.chapter_id == Chapter.id)
        .where(FC.foreshadowing_id == foreshadowing_id, FC.role == role)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def restatus(session: AsyncSession, row: Foreshadowing) -> None:
    """챕터 행에서 상태를 다시 계산한다. 상태를 바꾸는 유일한 경로다."""
    await session.flush()
    roles = set(
        (await session.execute(select(FC.role).where(FC.foreshadowing_id == row.id))).scalars()
    )
    status: Status = (
        "orphaned" if "setup" not in roles else "resolved" if "payoff" in roles else "unresolved"
    )
    row.status = status


def _no_expr(role: str) -> Any:
    """목록 필터용 — 이 복선의 `role` 챕터 번호(없으면 NULL)."""
    return (
        select(Chapter.chapter_no)
        .join(FC, FC.chapter_id == Chapter.id)
        .where(FC.foreshadowing_id == Foreshadowing.id, FC.role == role)
        .scalar_subquery()
    )


def _overlap(stmt: Any, chapter_from: int | None, chapter_to: int | None) -> Any:
    """설치~회수 범위가 [from, to] 와 겹치는 복선(명세 §4.1). 미회수는 끝이 열려 있다."""
    setup, payoff = _no_expr("setup"), _no_expr("payoff")
    if chapter_to is not None:
        stmt = stmt.where((setup.is_(None)) | (setup <= chapter_to))
    if chapter_from is not None:
        stmt = stmt.where((payoff.is_(None)) | (payoff >= chapter_from))
    return stmt


async def status_counts(session: AsyncSession, project_id: UUID) -> StatusCounts:
    stmt = (
        select(Foreshadowing.status, func.count())
        .where(Foreshadowing.project_id == project_id)
        .group_by(Foreshadowing.status)
    )
    counts = {"unresolved": 0, "resolved": 0, "orphaned": 0}
    counts.update({status: n for status, n in await session.execute(stmt)})
    return StatusCounts(**counts)


# --- 복선 CRUD --------------------------------------------------------------


async def _link_characters(
    session: AsyncSession, row: Foreshadowing, character_ids: Sequence[UUID]
) -> None:
    for character_id in dict.fromkeys(character_ids):
        stmt = select(Character.id).where(
            Character.id == character_id,
            Character.project_id == row.project_id,
            Character.status == "active",
        )
        if (await session.execute(stmt)).scalar_one_or_none() is None:
            raise errors.LinkTargetNotFound(target_type="character", target_id=str(character_id))
        await session.execute(
            insert(ForeshadowingCharacter)
            .values(foreshadowing_id=row.id, character_id=character_id, project_id=row.project_id)
            .on_conflict_do_nothing()
        )


async def similar(
    session: AsyncSession, project_id: UUID, title: str, exclude: UUID
) -> list[SimilarCandidate]:
    """명세 12항 "중복 복선 등록": 막지 않고 후보만 알린다. 제목이 서로를 포함하면 후보다."""
    t = func.lower(func.btrim(literal(title)))
    stmt = (
        select(Foreshadowing.id, Foreshadowing.title)
        .where(
            Foreshadowing.project_id == project_id,
            Foreshadowing.id != exclude,
            (func.strpos(func.lower(Foreshadowing.title), t) > 0)
            | (func.strpos(t, func.lower(Foreshadowing.title)) > 0),
        )
        .order_by(Foreshadowing.created_at)
        .limit(5)
    )
    return [SimilarCandidate(foreshadowing_id=i, title=n) for i, n in await session.execute(stmt)]


async def create(
    session: AsyncSession, project_id: UUID, body: ForeshadowingCreate
) -> tuple[ForeshadowingResponse, list[SimilarCandidate]]:
    await _chapter(session, project_id, body.setup_chapter_id, "setup_chapter_id")
    row = Foreshadowing(project_id=project_id, title=body.title, description=body.description)
    session.add(row)
    await session.flush()
    session.add(
        FC(
            project_id=project_id,
            foreshadowing_id=row.id,
            chapter_id=body.setup_chapter_id,
            role="setup",
        )
    )
    await _link_characters(session, row, body.linked_character_ids)
    await restatus(session, row)
    return await _one(session, row), await similar(session, project_id, body.title, row.id)


async def list_foreshadowings(
    session: AsyncSession,
    project_id: UUID,
    limit: int,
    cursor: Cursor,
    *,
    status: Status | None = None,
    chapter_from: int | None = None,
    chapter_to: int | None = None,
    linked_character_id: UUID | None = None,
    linked_event_id: UUID | None = None,
) -> list[Foreshadowing]:
    """먼저 만든 복선부터(화면의 F01, F02 … 순)."""
    stmt = select(Foreshadowing).where(Foreshadowing.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Foreshadowing.status == status)
    stmt = _overlap(stmt, chapter_from, chapter_to)
    if linked_character_id is not None:
        stmt = stmt.where(
            exists().where(
                ForeshadowingCharacter.foreshadowing_id == Foreshadowing.id,
                ForeshadowingCharacter.character_id == linked_character_id,
            )
        )
    if linked_event_id is not None:
        stmt = stmt.where(
            exists().where(
                ForeshadowingEvent.foreshadowing_id == Foreshadowing.id,
                ForeshadowingEvent.event_id == linked_event_id,
            )
        )
    stmt = stmt.order_by(Foreshadowing.created_at, Foreshadowing.id).limit(limit + 1)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Foreshadowing.created_at, Foreshadowing.id)
            > tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def get(
    session: AsyncSession, project_id: UUID, foreshadowing_id: UUID
) -> ForeshadowingResponse:
    return await _one(session, await _get(session, project_id, foreshadowing_id))


async def update(
    session: AsyncSession, project_id: UUID, foreshadowing_id: UUID, body: ForeshadowingUpdate
) -> ForeshadowingResponse:
    row = await _get(session, project_id, foreshadowing_id, lock=True)
    if body.title is not None:
        row.title = body.title
    if "description" in body.model_fields_set:
        row.description = body.description
    if body.setup_chapter_id is not None:
        chapter = await _chapter(session, project_id, body.setup_chapter_id, "setup_chapter_id")
        payoff = await _role_no(session, row.id, "payoff")
        if payoff is not None and payoff < chapter.chapter_no:
            raise errors.InvalidPayoffChapter(
                f"회수 챕터({payoff})는 설치 챕터({chapter.chapter_no})보다 앞설 수 없습니다",
                setup_chapter=chapter.chapter_no,
                requested_payoff_chapter=payoff,
            )
        await session.execute(delete(FC).where(FC.foreshadowing_id == row.id, FC.role == "setup"))
        session.add(
            FC(project_id=project_id, foreshadowing_id=row.id, chapter_id=chapter.id, role="setup")
        )
        await restatus(session, row)
    return await _one(session, row)


async def remove(session: AsyncSession, project_id: UUID, foreshadowing_id: UUID) -> None:
    await session.delete(await _get(session, project_id, foreshadowing_id))
    await session.flush()


# --- 연결 챕터 · 회수 ---------------------------------------------------------


async def add_linked(
    session: AsyncSession, project_id: UUID, foreshadowing_id: UUID, chapter_id: UUID
) -> ForeshadowingResponse:
    """같은 챕터를 두 번 연결해도 한 번이다. 설치 챕터보다 앞서면 `INVALID_INPUT`(명세 §4.6)."""
    row = await _get(session, project_id, foreshadowing_id, lock=True)
    chapter = await _chapter(session, project_id, chapter_id, "chapter_id")
    setup = await _role_no(session, row.id, "setup")
    if setup is not None and chapter.chapter_no < setup:
        raise errors.InvalidInput(
            f"연결 챕터({chapter.chapter_no})는 설치 챕터({setup})보다 앞설 수 없습니다",
            field="chapter_id",
        )
    await session.execute(
        insert(FC)
        .values(
            project_id=project_id, foreshadowing_id=row.id, chapter_id=chapter.id, role="linked"
        )
        .on_conflict_do_nothing()
    )
    return await _one(session, row)


async def remove_linked(
    session: AsyncSession, project_id: UUID, foreshadowing_id: UUID, chapter_id: UUID
) -> None:
    row = await _get(session, project_id, foreshadowing_id, lock=True)
    result = await session.execute(
        delete(FC).where(
            FC.foreshadowing_id == row.id, FC.chapter_id == chapter_id, FC.role == "linked"
        )
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise errors.LinkedChapterNotFound()


async def set_payoff(
    session: AsyncSession, project_id: UUID, foreshadowing_id: UUID, chapter_id: UUID
) -> ForeshadowingResponse:
    """회수 챕터를 기록(또는 교체)한다. 설치 챕터보다 앞서면 `INVALID_PAYOFF_CHAPTER`."""
    row = await _get(session, project_id, foreshadowing_id, lock=True)
    chapter = await _chapter(session, project_id, chapter_id, "payoff_chapter_id")
    setup = await _role_no(session, row.id, "setup")
    if setup is not None and chapter.chapter_no < setup:
        raise errors.InvalidPayoffChapter(
            f"회수 챕터({chapter.chapter_no})는 설치 챕터({setup})보다 앞설 수 없습니다",
            setup_chapter=setup,
            requested_payoff_chapter=chapter.chapter_no,
        )
    await session.execute(delete(FC).where(FC.foreshadowing_id == row.id, FC.role == "payoff"))
    session.add(
        FC(project_id=project_id, foreshadowing_id=row.id, chapter_id=chapter.id, role="payoff")
    )
    await restatus(session, row)
    return await _one(session, row)


async def clear_payoff(
    session: AsyncSession, project_id: UUID, foreshadowing_id: UUID
) -> ForeshadowingResponse:
    row = await _get(session, project_id, foreshadowing_id, lock=True)
    result = await session.execute(
        delete(FC).where(FC.foreshadowing_id == row.id, FC.role == "payoff")
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise errors.PayoffNotSet()
    await restatus(session, row)
    return await _one(session, row)


async def link(
    session: AsyncSession, project_id: UUID, foreshadowing_id: UUID, target_id: UUID
) -> ForeshadowingResponse:
    row = await _get(session, project_id, foreshadowing_id, lock=True)
    await _link_characters(session, row, [target_id])
    return await _one(session, row)


async def unlink(
    session: AsyncSession, project_id: UUID, foreshadowing_id: UUID, target_id: UUID
) -> None:
    """이미 없는 연결이어도 204 다(명세 §4.14 는 404 를 정하지 않았다)."""
    row = await _get(session, project_id, foreshadowing_id, lock=True)
    await session.execute(
        delete(ForeshadowingCharacter).where(
            ForeshadowingCharacter.foreshadowing_id == row.id,
            ForeshadowingCharacter.character_id == target_id,
        )
    )


# --- 미회수 · 안내 · 타임라인 --------------------------------------------------


async def _unresolved_rows(
    session: AsyncSession, project_id: UUID, current_chapter: int
) -> list[tuple[Foreshadowing, int]]:
    """(복선, 설치 챕터 번호). 현재 챕터보다 뒤에 설치된 복선은 아직 "경과" 가 없어 뺀다."""
    setup = _no_expr("setup").label("setup_no")
    stmt = (
        select(Foreshadowing, setup)
        .where(Foreshadowing.project_id == project_id, Foreshadowing.status == "unresolved")
        .order_by(setup, Foreshadowing.created_at)
    )
    return [
        (f, n) for f, n in await session.execute(stmt) if n is not None and n <= current_chapter
    ]


async def unresolved(
    session: AsyncSession, project_id: UUID, current_chapter: int
) -> UnresolvedResponse:
    # sort 의 두 값(elapsed_desc · setup_chapter_asc)은 같은 순서다: 경과 = 현재 − 설치.
    return UnresolvedResponse(
        current_chapter=current_chapter,
        unresolved=[
            UnresolvedItem(
                foreshadowing_id=f.id,
                title=f.title,
                setup_chapter=n,
                elapsed_chapters=current_chapter - n,
            )
            for f, n in await _unresolved_rows(session, project_id, current_chapter)
        ],
    )


async def advisories(
    session: AsyncSession, project_id: UUID, current_chapter: int
) -> list[Advisory]:
    rows = await _unresolved_rows(session, project_id, current_chapter)
    refs = await _refs(session, [f.id for f, _ in rows])
    out = []
    for f, n in rows:
        linked = [
            r.chapter_no for r in refs[f.id] if r.role == "linked" and r.chapter_no is not None
        ]
        out.append(
            Advisory(
                foreshadowing_id=f.id,
                message=advisory_message(n, f.title),
                setup_chapter=n,
                latest_linked_chapter=max(linked, default=None),
                priority=priority_of(current_chapter - n),
            )
        )
    return out


async def timeline(
    session: AsyncSession,
    project_id: UUID,
    status: Status | None,
    chapter_from: int | None,
    chapter_to: int | None,
) -> list[TimelineTrack]:
    stmt = select(Foreshadowing).where(Foreshadowing.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Foreshadowing.status == status)
    stmt = _overlap(stmt, chapter_from, chapter_to).order_by(
        Foreshadowing.created_at, Foreshadowing.id
    )
    rows = list((await session.execute(stmt)).scalars())
    refs = await _refs(session, [f.id for f in rows])
    return [
        TimelineTrack(
            foreshadowing_id=f.id,
            title=f.title,
            status=f.status,
            markers=[
                Marker(chapter=r.chapter_no, type=r.role)
                for r in refs[f.id]
                if r.chapter_no is not None
            ],
            is_open=f.status != "resolved",
        )
        for f in rows
    ]


async def of_chapter(
    session: AsyncSession, project_id: UUID, chapter_id: UUID
) -> list[ChapterForeshadowing]:
    """챕터 삭제 전 확인용(명세 §4.15). `setup` 이 있으면 삭제 후 그 복선은 orphaned 가 된다."""
    stmt = (
        select(Foreshadowing.id, Foreshadowing.title, FC.role)
        .join(FC, FC.foreshadowing_id == Foreshadowing.id)
        .where(Foreshadowing.project_id == project_id, FC.chapter_id == chapter_id)
        .order_by(Foreshadowing.created_at)
    )
    return [
        ChapterForeshadowing(foreshadowing_id=i, title=t, role=r)
        for i, t, r in await session.execute(stmt)
    ]
