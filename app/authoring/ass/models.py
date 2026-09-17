from typing import Any
from uuid import UUID

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class Character(Base, Timestamps):
    __tablename__ = "characters"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")
    role: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)


class CharacterAttribute(Base, Timestamps):
    """CharacterPersonalityTags + CharacterCoreValues 를 category 로 통합한 테이블(§5)."""

    __tablename__ = "character_attributes"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    character_id: Mapped[UUID] = fk_uuid()
    category: Mapped[str] = mapped_column(String(30))  # personality_tag | core_value
    value: Mapped[str] = mapped_column(String(100))


class CharacterEditHistory(Base, Timestamps):
    __tablename__ = "character_edit_histories"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    character_id: Mapped[UUID] = fk_uuid()
    edited_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
