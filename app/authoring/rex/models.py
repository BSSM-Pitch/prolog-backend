from uuid import UUID

from sqlalchemy import ARRAY, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class WorldRule(Base, Timestamps):
    """REX 확장 필드 버전이 정본 (CLAUDE.md §2 규칙 6)."""

    __tablename__ = "world_rules"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    # title · category 는 ERD 에 없지만 유용해 유지한다 (명세 수정 제안).
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50))
    violation_keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    origin: Mapped[str] = mapped_column(String(20), default="user_added")
    """ai_extracted | user_added — ERD 어휘. 구 manual/ai 가 아니다."""
    evidence: Mapped[str | None] = mapped_column(Text)
    extraction_job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source_chapter_no: Mapped[int | None] = mapped_column(Integer)
