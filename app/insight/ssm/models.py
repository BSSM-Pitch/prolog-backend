from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class StructureMap(Base, Timestamps):
    __tablename__ = "structure_maps"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    manuscript_id: Mapped[UUID] = fk_uuid()  # UNIQUE
    acts: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    edges: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class StructureNode(Base, Timestamps):
    """노드는 PATCH .../nodes/{id} 로 개별 수정된다 → 통짜 JSON 불가(§9)."""

    __tablename__ = "structure_nodes"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    map_id: Mapped[UUID] = fk_uuid()
    node_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    chapter_no: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # TODO(§12-2): 재분석 시 is_user_edited=true 노드 병합 정책 미결.
    is_user_edited: Mapped[bool] = mapped_column(Boolean, default=False)


class StructureNodeCharacter(Base, Timestamps):
    __tablename__ = "structure_node_characters"
    __table_args__ = {"schema": "insight"}

    node_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    character_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = fk_uuid()
