from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class CharacterDraft(Base, Timestamps):
    __tablename__ = "character_drafts"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class CharacterDraftItem(Base, Timestamps):
    __tablename__ = "character_draft_items"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    draft_id: Mapped[UUID] = fk_uuid()
    name: Mapped[str] = mapped_column(String(100))
    # 필드명은 ASS 기준이 정본(규칙 5):
    # personality_tags / core_values / influence_relations / emotion_keywords
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    character_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
