from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class Event(Base, Timestamps):
    __tablename__ = "events"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    chapter_id: Mapped[UUID] = fk_uuid()
    description: Mapped[str] = mapped_column(Text)
    emotion_keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    occurred_order: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class EventCharacter(Base, Timestamps):
    __tablename__ = "event_characters"
    __table_args__ = {"schema": "insight"}

    event_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    character_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = fk_uuid()
    role: Mapped[str | None] = mapped_column(String(30))


class Conflict(Base, Timestamps):
    __tablename__ = "conflicts"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    event_id: Mapped[UUID] = fk_uuid()
    character_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    rule_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # AI 가 남긴 충돌은 `ai`, AI 가 끝내 실패해 룰 후보만 남긴 것은 `rule`(advice 없음).
    detected_by: Mapped[str] = mapped_column(String(20), default="rule")  # rule | ai
    severity: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # 0010
    advice: Mapped[str | None] = mapped_column(Text)
    suppression_key: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    chapter_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    conflict_target: Mapped[str | None] = mapped_column(Text)
    matched_keyword: Mapped[str | None] = mapped_column(Text)
    modified_content: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConflictSuppression(Base, Timestamps):
    __tablename__ = "conflict_suppressions"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    # sha256(character_id : rule_id : normalize(event_description))  — §7
    suppression_key: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
