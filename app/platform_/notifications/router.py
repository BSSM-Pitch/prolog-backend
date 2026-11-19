from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, status

from app.core import errors
from app.core.deps import User
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.platform_.notifications import service
from app.platform_.notifications.schemas import (
    NotificationSettingResponse,
    NotificationSettingUpdate,
    ReadAllResponse,
)

router = APIRouter(prefix="/notifications", tags=["NOTI"])
# 명세 §3 의 6·7 은 `/users/me` 아래다. 같은 모듈이 갖는다.
settings_router = APIRouter(prefix="/users/me/notification-settings", tags=["NOTI"])

NotificationId = Annotated[UUID, Path(alias="notificationId")]
NotFound = raises(errors.NotificationNotFound)


# 목록 · 조회 · 읽음 표시는 응답에 초대의 현재 상태(TEAM · PRJ)를 붙이므로 조합 레이어
# (`app/api/router.py`)에 있다. 여기 남은 것은 NOTI 데이터만 쓰는 엔드포인트다.


# 리터럴 경로가 {notificationId} 보다 먼저 등록돼야 한다. 순서가 곧 매칭 규칙이다.
@router.patch(
    "/read-all",
    summary="내 알림을 모두 읽음으로 표시한다",
    response_model=Envelope[ReadAllResponse],
)
async def mark_all_read(session: Session, user: User) -> dict[str, Any]:
    """`updated_count` 는 이번 호출로 새로 읽음이 된 개수다."""
    return ok({"updated_count": await service.mark_all_read(session, user.id)})


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


@settings_router.get(
    "", summary="내 알림 설정", response_model=Envelope[list[NotificationSettingResponse]]
)
async def get_settings(session: Session, user: User) -> dict[str, Any]:
    """알림 유형 다섯 개(팀 초대 · 프로젝트 초대 · 팀 합류 · 멘션 · 시스템)마다 채널 설정.

    바꾼 적 없는 유형은 둘 다 `true` 다. 목록이 고정이라 페이지로 자르지 않는다.
    """
    return ok(await service.settings(session, user.id))


@settings_router.patch(
    "", summary="알림 설정을 바꾼다", response_model=Envelope[NotificationSettingResponse]
)
async def update_setting(
    body: NotificationSettingUpdate, session: Session, user: User
) -> dict[str, Any]:
    """유형 하나의 채널을 바꾼다. 보낸 채널만 바뀐다.

    `in_app_enabled = false` 면 그 유형의 알림이 더는 만들어지지 않는다. 이메일은 아직 발송
    경로가 없어 `email_enabled` 는 저장만 된다(연동이 생기면 그때 쓴다).
    """
    return ok(await service.update_setting(session, user.id, body))
