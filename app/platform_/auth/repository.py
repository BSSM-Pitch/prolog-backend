from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform_.auth.models import RefreshToken, User


async def get_user_by_provider(
    session: AsyncSession, auth_provider: str, provider_user_id: str
) -> User | None:
    """기존 사용자 판별은 (auth_provider, provider_user_id) 다. 이메일이 아니다."""
    stmt = select(User).where(
        User.auth_provider == auth_provider, User.provider_user_id == provider_user_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.get(User, user_id)


async def username_exists(session: AsyncSession, username: str) -> bool:
    stmt = select(User.id).where(User.username == username).limit(1)
    return (await session.execute(stmt)).first() is not None


async def add_user(session: AsyncSession, user: User) -> User:
    session.add(user)
    await session.flush()
    return user


async def add_refresh_token(
    session: AsyncSession, user_id: UUID, token_hash: str, expires_at: datetime
) -> RefreshToken:
    row = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    session.add(row)
    await session.flush()
    return row


async def get_refresh_token(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    return (await session.execute(stmt)).scalar_one_or_none()


async def revoke_refresh_token(session: AsyncSession, token_hash: str) -> None:
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    await session.execute(stmt)
