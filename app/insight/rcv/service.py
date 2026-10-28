"""RCV CRUD. 프로젝트 권한은 라우터, 참조의 프로젝트 일치는 여기서 검사한다.

원고별 이력을 섞지 않는다. 번호는 Chapter에서 읽어 챕터 재배열도 반영한다.
관계 생성은 인물 두 행을 고정 순서로, 이력 변경은 관계 행을 잠가 경합을 직렬화한다.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.authoring.ass.models import Character
from app.content.manuscripts.models import Chapter, Manuscript
from app.core import errors
from app.insight.rcv.models import Relationship, RelationshipHistory
from app.insight.rcv.schemas import (
    RelationshipCreate,
    RelationshipEdge,
    RelationshipEntryCreate,
    RelationshipHistoryEntry,
    RelationshipMindmap,
    RelationshipNode,
    RelationshipResponse,
    RelationshipSnapshot,
)


async def manuscript(session: AsyncSession, project_id: UUID, manuscript_id: UUID) -> None:
    exists = await session.scalar(
        select(Manuscript.id).where(
            Manuscript.id == manuscript_id, Manuscript.project_id == project_id
        )
    )
    if exists is None:
        raise errors.ManuscriptNotFound()


async def get(
    session: AsyncSession, project_id: UUID, relationship_id: UUID, *, lock: bool = False
) -> Relationship:
    stmt = select(Relationship).where(
        Relationship.id == relationship_id, Relationship.project_id == project_id
    )
    row = await session.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise errors.RelationshipNotFound()
    return row


async def responses(
    session: AsyncSession, rows: Sequence[Relationship], manuscript_id: UUID | None = None
) -> list[RelationshipResponse]:
    histories: dict[UUID, list[RelationshipHistoryEntry]] = {row.id: [] for row in rows}
    if histories:
        stmt = (
            select(RelationshipHistory, Chapter)
            .join(Chapter, Chapter.id == RelationshipHistory.chapter_id)
            .where(RelationshipHistory.relationship_id.in_(histories))
            .order_by(Chapter.chapter_no, Chapter.id)
        )
        if manuscript_id:
            stmt = stmt.where(Chapter.manuscript_id == manuscript_id)
        for entry, chapter in await session.execute(stmt):
            histories[entry.relationship_id].append(
                RelationshipHistoryEntry(
                    chapter_id=chapter.id,
                    manuscript_id=chapter.manuscript_id,
                    chapter=chapter.chapter_no,
                    state=entry.state or "상태 미정",
                    trust=entry.trust,
                    event_title=entry.note,
                )
            )
    return [
        RelationshipResponse(
            relationship_id=row.id,
            project_id=row.project_id,
            source_character_id=row.source_character_id,
            target_character_id=row.target_character_id,
            history=histories[row.id],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


async def list_rows(
    session: AsyncSession,
    project_id: UUID,
    limit: int,
    cursor: tuple[datetime, UUID] | None,
    character_id: UUID | None = None,
) -> list[Relationship]:
    stmt = select(Relationship).where(Relationship.project_id == project_id)
    if character_id:
        stmt = stmt.where(
            or_(
                Relationship.source_character_id == character_id,
                Relationship.target_character_id == character_id,
            )
        )
    if cursor:
        stmt = stmt.where(tuple_(Relationship.created_at, Relationship.id) > cursor)
    return list(
        (
            await session.scalars(
                stmt.order_by(Relationship.created_at, Relationship.id).limit(limit + 1)
            )
        ).all()
    )


async def _chapter(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> Chapter:
    # SHARE는 챕터 삭제/번호 변경과 이력 기록의 경합을 막는다.
    row = await session.scalar(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.project_id == project_id)
        .with_for_update(read=True)
    )
    if row is None:
        raise errors.ChapterNotFound()
    return row


async def _record(
    session: AsyncSession,
    row: Relationship,
    chapter: Chapter,
    body: RelationshipEntryCreate,
    overwrite: bool,
) -> RelationshipHistoryEntry:
    existing = await session.get(RelationshipHistory, (row.id, chapter.id))
    if existing and not overwrite:
        raise errors.DuplicateChapterRecord(
            existing_entry={
                "chapter": chapter.chapter_no,
                "state": existing.state,
                "trust": existing.trust,
                "event_title": existing.note,
            }
        )
    if existing is None:
        existing = RelationshipHistory(
            relationship_id=row.id,
            chapter_id=chapter.id,
            project_id=row.project_id,
            chapter_no=chapter.chapter_no,
        )
        session.add(existing)
    existing.state, existing.trust, existing.note = body.state, body.trust, body.event_title
    existing.chapter_no = chapter.chapter_no
    row.updated_at = func.now()
    await session.flush()
    return RelationshipHistoryEntry(
        chapter_id=chapter.id,
        manuscript_id=chapter.manuscript_id,
        chapter=chapter.chapter_no,
        state=body.state,
        trust=body.trust,
        event_title=body.event_title,
    )


async def create(
    session: AsyncSession, project_id: UUID, body: RelationshipCreate
) -> RelationshipResponse:
    ids = sorted([body.source_character_id, body.target_character_id])
    if ids[0] == ids[1]:
        raise errors.InvalidInput("서로 다른 인물이어야 합니다")
    people = list(
        (
            await session.scalars(
                select(Character)
                .where(Character.project_id == project_id, Character.id.in_(ids))
                .order_by(Character.id)
                .with_for_update()
            )
        ).all()
    )
    if len(people) != 2:
        raise errors.CharacterNotFound()
    chapter = await _chapter(session, project_id, body.initial_history.chapter_id)
    row = await session.scalar(
        select(Relationship)
        .where(
            Relationship.project_id == project_id,
            Relationship.source_character_id.in_(ids),
            Relationship.target_character_id.in_(ids),
        )
        .with_for_update()
    )
    if row:
        previous = (await responses(session, [row], chapter.manuscript_id))[0]
        if previous.history:
            raise errors.DuplicateRelationship(relationship_id=str(row.id))
        # 같은 인물 쌍을 다른 원고에서 처음 기록할 때는 기존 관계에 그 원고의 이력을 추가한다.
    else:
        row = Relationship(
            project_id=project_id,
            source_character_id=body.source_character_id,
            target_character_id=body.target_character_id,
        )
        session.add(row)
        await session.flush()
    await _record(session, row, chapter, body.initial_history, False)
    await session.refresh(row)
    return (await responses(session, [row], chapter.manuscript_id))[0]


async def record(
    session: AsyncSession,
    project_id: UUID,
    relationship_id: UUID,
    body: RelationshipEntryCreate,
    overwrite: bool,
) -> RelationshipHistoryEntry:
    row = await get(session, project_id, relationship_id, lock=True)
    return await _record(
        session, row, await _chapter(session, project_id, body.chapter_id), body, overwrite
    )


async def delete(session: AsyncSession, project_id: UUID, relationship_id: UUID) -> None:
    await session.delete(await get(session, project_id, relationship_id, lock=True))
    await session.flush()


async def delete_record(
    session: AsyncSession, project_id: UUID, relationship_id: UUID, chapter_id: UUID
) -> None:
    row = await get(session, project_id, relationship_id, lock=True)
    entry = await session.get(RelationshipHistory, (row.id, chapter_id))
    if entry is None:
        raise errors.RelationshipHistoryNotFound()
    await session.delete(entry)
    row.updated_at = func.now()
    await session.flush()


def snapshot(row: RelationshipResponse, chapter: int) -> RelationshipSnapshot:
    entry = next((h for h in reversed(row.history) if h.chapter <= chapter), None)
    return RelationshipSnapshot(
        relationship_id=row.relationship_id,
        requested_chapter=chapter,
        resolved_chapter=entry.chapter if entry else None,
        state=entry.state if entry else None,
        trust=entry.trust if entry else None,
        is_carried_forward=entry is not None and entry.chapter != chapter,
    )


async def mindmap(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    chapter: int,
    character_id: UUID | None,
) -> RelationshipMindmap:
    await manuscript(session, project_id, manuscript_id)
    stmt = select(Relationship).where(Relationship.project_id == project_id)
    if character_id:
        stmt = stmt.where(
            or_(
                Relationship.source_character_id == character_id,
                Relationship.target_character_id == character_id,
            )
        )
    rows = await responses(session, (await session.scalars(stmt)).all(), manuscript_id)
    edges = []
    ids: set[UUID] = set()
    for row in rows:
        point = snapshot(row, chapter)
        if point.state is None or point.resolved_chapter is None:
            continue
        ids.update((row.source_character_id, row.target_character_id))
        edges.append(
            RelationshipEdge(
                relationship_id=row.relationship_id,
                source_character_id=row.source_character_id,
                target_character_id=row.target_character_id,
                state=point.state,
                trust=point.trust,
                resolved_chapter=point.resolved_chapter,
                is_carried_forward=point.is_carried_forward,
            )
        )
    nodes = [
        RelationshipNode(character_id=p.id, name=p.name)
        for p in await session.scalars(
            select(Character)
            .where(Character.project_id == project_id, Character.id.in_(ids))
            .order_by(Character.name, Character.id)
        )
    ]
    return RelationshipMindmap(chapter=chapter, nodes=nodes, edges=edges)
