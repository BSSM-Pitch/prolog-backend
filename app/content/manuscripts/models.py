from uuid import UUID

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class Manuscript(Base, Timestamps):
    __tablename__ = "manuscripts"
    __table_args__ = {"schema": "content"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    title: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(20))  # editor | upload, 생성 후 불변(트리거)
    file_key: Mapped[str | None] = mapped_column(String(512))  # S3 key. URL 저장 금지(§9)
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    extraction_job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class Chapter(Base, Timestamps):
    __tablename__ = "chapters"
    __table_args__ = {"schema": "content"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()  # 테넌시용 비정규화(§5)
    manuscript_id: Mapped[UUID] = fk_uuid()  # project 직결이 아니다(§5 최우선 결정사항)
    chapter_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")


class ManuscriptVersion(Base, Timestamps):
    __tablename__ = "manuscript_versions"
    __table_args__ = {"schema": "content"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    manuscript_id: Mapped[UUID] = fk_uuid()
    chapter_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source: Mapped[str] = mapped_column(String(10))  # editor | upload (0008)
