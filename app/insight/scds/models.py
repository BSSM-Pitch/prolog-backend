from uuid import UUID

from sqlalchemy import ARRAY, Integer, String, Text
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
    # 룰 검출 시점에 만들고 advice 는 나중에 채운다. AI 실패는 데이터 손실이 아니다(§7).
    detected_by: Mapped[str] = mapped_column(String(20), default="rule")  # rule | ai
    severity: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="open")
    advice: Mapped[str | None] = mapped_column(Text)
    suppression_key: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class ConflictSuppression(Base, Timestamps):
    __tablename__ = "conflict_suppressions"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    # sha256(character_id : rule_id : normalize(event_description))  — §7
    suppression_key: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
