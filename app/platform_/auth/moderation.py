"""Operator-only account suspension; deliberately has no public HTTP route."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.platform_.auth.models import RefreshToken, User


async def ban_account(session: AsyncSession, user_id: UUID, *, reason: str) -> User:
    """Caller commits the ban and session revocation in one transaction."""
    reason = reason.strip()
    if not reason or len(reason) > 500:
        raise errors.InvalidInput("정지 사유는 1~500자로 입력해 주세요")
    user = await session.get(User, user_id, with_for_update=True, populate_existing=True)
    if user is None:
        raise errors.UserNotFound()
    now = datetime.now(UTC)
    user.banned_at = user.banned_at or now
    user.ban_reason = reason
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.flush()
    return user
