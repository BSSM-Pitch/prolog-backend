from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class CharacterDraft(Base, Timestamps):
    __tablename__ = "character_drafts"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # ERD 에 없지만 화면의 자연어 원문을 담는다 (명세 수정 제안).
    source_text: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(100))  # 확정 시 필수
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # 확정 결과 추적. merge 로 확정하면 기존 캐릭터 id 가 들어간다.
    confirmed_character_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class CharacterDraftItem(Base, Timestamps):
    __tablename__ = "character_draft_items"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    draft_id: Mapped[UUID] = fk_uuid()
    # character_attributes 와 같은 모양이다 — 확정 시 그대로 승계된다.
    category: Mapped[str] = mapped_column(String(30))
    value: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(20))  # ai_extracted | user_added
