"""Exact identity is conservative; semantic matches require explicit author merging."""

import unicodedata
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.authoring.rex.models import WorldRule


def identity(description: str) -> str:
    return "".join(unicodedata.normalize("NFKC", description).casefold().split()).rstrip(".。")


async def locked_rules(session: AsyncSession, project_id: UUID) -> list[WorldRule]:
    # The same transaction lock is used by manual creation and extraction confirm,
    # including when there are no rule rows yet. It prevents cross-job duplicates.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope))"), {"scope": f"rules:{project_id}"}
    )
    return list(
        (
            await session.execute(
                select(WorldRule)
                .where(WorldRule.project_id == project_id)
                .order_by(WorldRule.created_at, WorldRule.id)
                .with_for_update()
            )
        ).scalars()
    )
