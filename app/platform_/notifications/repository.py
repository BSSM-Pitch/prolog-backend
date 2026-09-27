from datetime import datetime
from uuid import UUID

from sqlalchemy import func, literal, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform_.notifications.models import Notification, NotificationSetting


async def get(session: AsyncSession, notification_id: UUID, user_id: UUID) -> Notification | None:
    """남의 알림은 없는 것으로 취급한다 (명세 §1.4 "타인의 알림 포함")."""
    stmt = select(Notification).where(
        Notification.id == notification_id, Notification.user_id == user_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_user(
    session: AsyncSession,
    user_id: UUID,
    limit: int,
    cursor: tuple[datetime, UUID] | None,
    unread_only: bool = False,
    type_: str | None = None,
) -> list[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit + 1)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if type_ is not None:
        stmt = stmt.where(Notification.type == type_)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Notification.created_at, Notification.id)
            < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def unread_count(session: AsyncSession, user_id: UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    return (await session.execute(stmt)).scalar_one()


async def mark_all_read(session: AsyncSession, user_id: UUID) -> int:
    stmt = (
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=func.now())
        .returning(Notification.id)
    )
    return len((await session.execute(stmt)).scalars().all())


async def add(session: AsyncSession, notification: Notification) -> Notification:
    session.add(notification)
    await session.flush()
    await session.refresh(notification)
    return notification


async def settings_for(session: AsyncSession, user_id: UUID) -> NotificationSetting | None:
    stmt = select(NotificationSetting).where(NotificationSetting.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()
