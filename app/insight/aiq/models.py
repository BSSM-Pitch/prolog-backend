from uuid import UUID

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class QaThread(Base, Timestamps):
    """ManuscriptQuestions 를 대체한다 — 그 테이블은 존재하지 않는다(§5)."""

    __tablename__ = "qa_threads"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    manuscript_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    chapter_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    scope: Mapped[str] = mapped_column(String(20))  # project | chapter | selection
    selection_start: Mapped[int | None] = mapped_column(Integer)
    selection_end: Mapped[int | None] = mapped_column(Integer)
    # TODO(§12-4): scope=selection 오프셋 무효화 정책 미결. 스냅샷 컬럼은 미리 두었다.
    selected_text: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class QaMessage(Base, Timestamps):
    __tablename__ = "qa_messages"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    thread_id: Mapped[UUID] = fk_uuid()
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str | None] = mapped_column(String(20))
