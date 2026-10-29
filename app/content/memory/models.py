"""원고 기억 — 원문 조각 (0014). DDL 정본은 마이그레이션이다."""

from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk

# prolog-ai core.embeddings.EMBEDDING_DIM 과 같아야 한다. 바꾸면 마이그레이션도 바꾼다.
EMBEDDING_DIM = 1536


class Passage(Base, Timestamps):
    __tablename__ = "passages"
    __table_args__ = {"schema": "content"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    manuscript_id: Mapped[UUID] = fk_uuid()
    chapter_id: Mapped[UUID] = fk_uuid()
    seq: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(32))
    source_hash: Mapped[str] = mapped_column(String(32))
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
