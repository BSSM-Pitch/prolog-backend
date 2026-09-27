"""NOTI (Ring 1).

알림은 **시스템 내부 이벤트로만** 만들어진다 — 명세 §3 이 공개 생성 API 를 두지 않는다.
생성 경로는 Outbox 릴레이가 보낸 이벤트를 조합 레이어가 받아 `create` 를 호출하는 것뿐이다.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.platform_.notifications import repository as repo
from app.platform_.notifications.models import Notification
from app.platform_.notifications.schemas import NotificationResponse, RelatedRef

IN_APP = "in_app"


def _response(row: Notification) -> NotificationResponse:
    related = (
        RelatedRef(type=row.resource_type, id=row.resource_id)
        if row.resource_type and row.resource_id
        else None
    )
    return NotificationResponse(
        notification_id=row.id,
        user_id=row.user_id,
        type=row.type,
        title=row.title,
        body=row.body,
        related_ref=related,
        channels_sent=list(row.channels_sent),
        read_at=row.read_at,
        created_at=row.created_at,
    )


async def create(
    session: AsyncSession,
    *,
    user_id: UUID,
    type: str,
    title: str,
    body: str | None = None,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> NotificationResponse | None:
    """설정이 막고 있으면 만들지 않고 None 을 돌려준다.

    `channels_sent` 는 **실제 발송 결과**다(CLAUDE.md §8). 메일 발송 경로가 아직 없으므로
    항상 `["in_app"]` 이다 — 설정값을 그대로 베끼지 않는다.
    """
    settings_row = await repo.settings_for(session, user_id)
    if settings_row is not None:
        if not settings_row.in_app_enabled or type in settings_row.muted_types:
            return None

    row = await repo.add(
        session,
        Notification(
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            payload=payload or {},
            resource_type=resource_type,
            resource_id=resource_id,
            channels_sent=[IN_APP],
        ),
    )
    return _response(row)


async def list_notifications(
    session: AsyncSession,
    user_id: UUID,
    limit: int,
    cursor: tuple[datetime, UUID] | None,
    unread_only: bool = False,
    type_: str | None = None,
) -> list[Notification]:
    return await repo.list_for_user(session, user_id, limit, cursor, unread_only, type_)


def to_responses(rows: list[Notification]) -> list[NotificationResponse]:
    return [_response(row) for row in rows]


async def unread_count(session: AsyncSession, user_id: UUID) -> int:
    return await repo.unread_count(session, user_id)


async def _get(session: AsyncSession, notification_id: UUID, user_id: UUID) -> Notification:
    row = await repo.get(session, notification_id, user_id)
    if row is None:
        raise errors.NotificationNotFound()
    return row


async def get(session: AsyncSession, notification_id: UUID, user_id: UUID) -> NotificationResponse:
    return _response(await _get(session, notification_id, user_id))


async def set_read(
    session: AsyncSession, notification_id: UUID, user_id: UUID, read: bool
) -> NotificationResponse:
    row = await _get(session, notification_id, user_id)
    row.read_at = datetime.now(UTC) if read else None
    await session.flush()
    return _response(row)


async def mark_all_read(session: AsyncSession, user_id: UUID) -> int:
    return await repo.mark_all_read(session, user_id)


async def delete(session: AsyncSession, notification_id: UUID, user_id: UUID) -> None:
    await session.delete(await _get(session, notification_id, user_id))
    await session.flush()
