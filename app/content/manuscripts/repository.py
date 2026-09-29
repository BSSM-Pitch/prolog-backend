from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts.models import Chapter, Manuscript


async def count_by_projects(session: AsyncSession, project_ids: Sequence[UUID]) -> dict[UUID, int]:
    """프로젝트별 원고 수. 프로젝트 수만큼 쿼리를 날리지 않는다."""
    if not project_ids:
        return {}
    stmt = (
        select(Manuscript.project_id, func.count())
        .where(Manuscript.project_id.in_(project_ids))
        .group_by(Manuscript.project_id)
    )
    return {row[0]: row[1] for row in (await session.execute(stmt))}


async def chapter_counts(session: AsyncSession, manuscript_ids: Sequence[UUID]) -> dict[UUID, int]:
    """원고별 챕터 수. 같은 모듈의 테이블이라 여기서 센다."""
    if not manuscript_ids:
        return {}
    stmt = (
        select(Chapter.manuscript_id, func.count())
        .where(Chapter.manuscript_id.in_(manuscript_ids))
        .group_by(Chapter.manuscript_id)
    )
    return {row[0]: row[1] for row in (await session.execute(stmt))}


async def get_manuscript(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, *, lock: bool = False
) -> Manuscript | None:
    """`lock` 이면 행을 잠근다 — 업로드 콜백과 스위퍼가 같은 원고에 잡을 두 개 만들지 않게."""
    stmt = select(Manuscript).where(
        Manuscript.id == manuscript_id, Manuscript.project_id == project_id
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def lost_uploads(session: AsyncSession, before: datetime, limit: int) -> list[Manuscript]:
    """URL 은 발급됐는데 콜백이 오지 않은 원고. 콜백이 잡고 있는 행은 건너뛴다(SKIP LOCKED)."""
    stmt = (
        select(Manuscript)
        .where(
            Manuscript.source_type == "upload",
            Manuscript.status == "draft",
            Manuscript.file_key.is_not(None),
            Manuscript.extraction_job_id.is_(None),
            Manuscript.updated_at < before,
        )
        .order_by(Manuscript.updated_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars())


async def list_manuscripts(
    session: AsyncSession, project_id: UUID, limit: int, cursor: tuple[datetime, UUID] | None
) -> list[Manuscript]:
    stmt = (
        select(Manuscript)
        .where(Manuscript.project_id == project_id)
        .order_by(Manuscript.created_at.desc(), Manuscript.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Manuscript.created_at, Manuscript.id)
            < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def add_manuscript(session: AsyncSession, manuscript: Manuscript) -> Manuscript:
    session.add(manuscript)
    await session.flush()
    await session.refresh(manuscript)
    return manuscript


async def get_chapter(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> Chapter | None:
    stmt = select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_chapters(
    session: AsyncSession,
    project_id: UUID,
    limit: int,
    cursor: tuple[int, UUID] | None,
    manuscript_id: UUID | None = None,
) -> list[Chapter]:
    """화 번호 순, limit+1 건. 원고가 여럿이면 같은 번호가 겹치므로 id 로 타이를 끊는다."""
    stmt = (
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_no.asc(), Chapter.id.asc())
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Chapter.chapter_no, Chapter.id) > tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    if manuscript_id is not None:
        stmt = stmt.where(Chapter.manuscript_id == manuscript_id)
    return list((await session.execute(stmt)).scalars())


async def add_chapter(session: AsyncSession, chapter: Chapter) -> Chapter:
    session.add(chapter)
    await session.flush()
    await session.refresh(chapter)
    return chapter
