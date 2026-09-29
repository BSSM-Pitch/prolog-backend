from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.response import Page, PageMeta

# 명세 §2.1 의 예시 enum. 서버가 만드는 값이라 요청으로 들어오지 않는다.
NotificationType = Literal["team_invite", "project_invite", "team_joined", "mention", "system"]


class RelatedRef(BaseModel):
    """명세 §2.1 의 `related_ref`. DB 는 resource_type · resource_id 두 컬럼으로 갖는다."""

    type: str
    id: UUID


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
