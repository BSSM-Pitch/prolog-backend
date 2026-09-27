from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from app.core.deps import User
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import ok
from app.db.session import Session
from app.platform_.notifications import service
from app.platform_.notifications.schemas import NotificationRead

router = APIRouter(prefix="/notifications", tags=["NOTI"])

NotificationId = Annotated[UUID, Path(alias="notificationId")]


@router.get("")
async def list_notifications(
    session: Session,
    user: User,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    unread_only: Annotated[bool, Query()] = False,
    type: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    rows = await service.list_notifications(
        session, user.id, limit, decode_cursor(cursor) if cursor else None, unread_only, type
    )
    page, meta = next_cursor(rows, limit)
    meta["unread_count"] = await service.unread_count(session, user.id)
    return ok(service.to_responses(page), meta)


# 리터럴 경로가 {notificationId} 보다 먼저 등록돼야 한다. 순서가 곧 매칭 규칙이다.
@router.patch("/read-all")
async def mark_all_read(session: Session, user: User) -> dict[str, Any]:
    return ok({"updated_count": await service.mark_all_read(session, user.id)})


@router.get("/{notificationId}")
async def get_notification(
    notification_id: NotificationId, session: Session, user: User
) -> dict[str, Any]:
    return ok(await service.get(session, notification_id, user.id))


@router.patch("/{notificationId}")
async def set_read(
    notification_id: NotificationId, body: NotificationRead, session: Session, user: User
) -> dict[str, Any]:
    return ok(await service.set_read(session, notification_id, user.id, body.read))


@router.delete("/{notificationId}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: NotificationId, session: Session, user: User
) -> None:
    await service.delete(session, notification_id, user.id)
