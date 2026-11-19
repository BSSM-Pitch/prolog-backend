from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.response import Page, PageMeta

# 명세 §2.1 의 예시 enum. 서버가 만드는 값이라 요청으로 들어오지 않는다.
NotificationType = Literal["team_invite", "project_invite", "team_joined", "mention", "system"]
# 화면 31 의 행 순서다. 설정 조회는 이 다섯을 항상 다 돌려준다.
NOTIFICATION_TYPES: tuple[NotificationType, ...] = get_args(NotificationType)


InvitationStatus = Literal["pending", "accepted", "expired", "revoked"]
INVITATION_REF_TYPES = ("team_invitation", "project_invitation")


class RelatedRef(BaseModel):
    """명세 §2.1 의 `related_ref`. DB 는 resource_type · resource_id 두 컬럼으로 갖는다."""

    type: str
    id: UUID
    # ASSUMPTION: 명세에 없는 두 필드다. 알림에서 바로 초대를 수락하려면 수락 경로
    # `/teams/{teamId}/invitations/{invitationId}/accept` 의 teamId(또는 projectId)와
    # 초대가 아직 수락 가능한지가 필요하다. 초대 알림(`team_invitation` ·
    # `project_invitation`)에만 채운다 — 초대가 사라졌으면 둘 다 null 이다.
    parent_id: UUID | None = Field(
        default=None,
        description="초대가 속한 팀(team_invitation) 또는 프로젝트(project_invitation)의 ID",
    )
    status: InvitationStatus | None = Field(
        default=None,
        description=(
            "초대의 **지금** 상태. 저장 상태가 pending 이어도 기한이 지났으면 expired. "
            "pending 일 때만 수락할 수 있다"
        ),
    )


class NotificationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    notification_id: UUID
    user_id: UUID
    type: str
    title: str
    body: str | None
    related_ref: RelatedRef | None
    # 설정값이 아니라 실제 발송 결과다 (CLAUDE.md §8).
    channels_sent: list[str]
    read_at: datetime | None
    created_at: datetime


class NotificationRead(BaseModel):
    read: bool = Field(description="true 면 읽음, false 면 다시 안읽음으로 되돌린다")


class NotificationPageMeta(PageMeta):
    unread_count: int = Field(description="필터와 무관한 내 안읽은 알림 전체 개수")


class NotificationPage(Page[NotificationResponse]):
    meta: NotificationPageMeta


class ReadAllResponse(BaseModel):
    updated_count: int


class NotificationSettingResponse(BaseModel):
    """명세 §2.2. 바꾼 적 없는 유형은 기본값(둘 다 true)이다."""

    type: NotificationType
    in_app_enabled: bool
    email_enabled: bool = Field(
        description="이메일 수신 여부. 연동 계정이 없거나 발송 경로가 없으면 보내지 않는다"
    )


class NotificationSettingUpdate(BaseModel):
    """명세 §4.7. 보낸 채널만 바뀐다."""

    type: NotificationType
    in_app_enabled: bool | None = None
    email_enabled: bool | None = None
