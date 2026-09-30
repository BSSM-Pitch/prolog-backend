from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ARRAY, Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class Notification(Base, Timestamps):
    __tablename__ = "notifications"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    user_id: Mapped[UUID] = fk_uuid()
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # 설정값이 아니라 실제 발송 결과다 (CLAUDE.md §8).
    channels_sent: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationSetting(Base, Timestamps):
    """유형별 채널 설정(0007). 기본값에서 바꾼 유형만 행이 있다 — 없으면 둘 다 켜짐."""

    __tablename__ = "notification_settings"
    __table_args__ = {"schema": "platform"}

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    type: Mapped[str] = mapped_column(String(50), primary_key=True)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class EmailIntegration(Base, Timestamps):
    __tablename__ = "email_integrations"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    user_id: Mapped[UUID] = fk_uuid()
    # ASSUMPTION: 명세 미정의 테이블. 발송용 외부 메일 연동으로 해석했다.
    provider: Mapped[str] = mapped_column(String(30))
    email: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
