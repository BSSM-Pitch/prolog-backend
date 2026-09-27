from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts.models import Manuscript


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
