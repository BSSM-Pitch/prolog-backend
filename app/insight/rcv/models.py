from uuid import UUID

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class Relationship(Base, Timestamps):
    __tablename__ = "relationships"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    source_character_id: Mapped[UUID] = fk_uuid()
    target_character_id: Mapped[UUID] = fk_uuid()
    type: Mapped[str | None] = mapped_column(String(50))


class RelationshipHistory(Base, Timestamps):
    __tablename__ = "relationship_histories"
    __table_args__ = {"schema": "insight"}

    # PK (relationship_id, chapter_id) → DUPLICATE_CHAPTER_RECORD (409)
    relationship_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    chapter_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = fk_uuid()
    chapter_no: Mapped[int] = mapped_column(Integer)  # carry-forward 정렬 키(§9)
    state: Mapped[str | None] = mapped_column(String(50))
    trust: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
