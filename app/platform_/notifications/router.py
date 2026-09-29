from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from app.core import errors
from app.core.deps import User
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.platform_.notifications import service
from app.platform_.notifications.schemas import (
    NotificationPage,
    NotificationRead,
    NotificationResponse,
    ReadAllResponse,
)

router = APIRouter(prefix="/notifications", tags=["NOTI"])

NotificationId = Annotated[UUID, Path(alias="notificationId")]
NotFound = raises(errors.NotificationNotFound)


@router.get("", summary="내 알림 목록", response_model=NotificationPage)
async def list_notifications(
    session: Session,
    user: User,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    unread_only: Annotated[bool, Query(description="true 면 안읽은 알림만")] = False,
    type: Annotated[str | None, Query(description="알림 종류로 거른다. 예: team_invite")] = None,
) -> dict[str, Any]:
    """최근 알림부터. 커서 페이지네이션이다.

    `meta.unread_count` 는 필터와 무관하게 내 안읽은 알림 전체 개수다(배지용).
    """
    rows = await service.list_notifications(
        session, user.id, limit, decode_cursor(cursor) if cursor else None, unread_only, type
    )
    page, meta = next_cursor(rows, limit)
    meta["unread_count"] = await service.unread_count(session, user.id)
    return ok(service.to_responses(page), meta)


# 리터럴 경로가 {notificationId} 보다 먼저 등록돼야 한다. 순서가 곧 매칭 규칙이다.
@router.patch(
    "/read-all",
    summary="내 알림을 모두 읽음으로 표시한다",
    response_model=Envelope[ReadAllResponse],
)
async def mark_all_read(session: Session, user: User) -> dict[str, Any]:
    """`updated_count` 는 이번 호출로 새로 읽음이 된 개수다."""
    return ok({"updated_count": await service.mark_all_read(session, user.id)})


@router.get(
    "/{notificationId}",
    summary="알림을 조회한다",
    response_model=Envelope[NotificationResponse],
    responses=NotFound,
)
async def get_notification(
    notification_id: NotificationId, session: Session, user: User
) -> dict[str, Any]:
    """내 알림만 볼 수 있다. 남의 알림은 존재하지 않는 것으로 답한다."""
    return ok(await service.get(session, notification_id, user.id))


@router.patch(
    "/{notificationId}",
    summary="알림의 읽음 상태를 바꾼다",
    response_model=Envelope[NotificationResponse],
    responses=NotFound,
)
async def set_read(
    notification_id: NotificationId, body: NotificationRead, session: Session, user: User
) -> dict[str, Any]:
    """`read = false` 로 다시 안읽음으로 되돌릴 수 있다."""
    return ok(await service.set_read(session, notification_id, user.id, body.read))


@router.delete(
    "/{notificationId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="알림을 삭제한다",
    responses=NotFound,
)
async def delete_notification(
    notification_id: NotificationId, session: Session, user: User
) -> None:
    """내 알림만 지울 수 있다."""
    await service.delete(session, notification_id, user.id)
