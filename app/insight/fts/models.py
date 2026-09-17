from uuid import UUID

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class Foreshadowing(Base, Timestamps):
    __tablename__ = "foreshadowings"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="planted")
    setup_chapter_no: Mapped[int | None] = mapped_column(Integer)
    payoff_chapter_no: Mapped[int | None] = mapped_column(Integer)


class ForeshadowingChapter(Base, Timestamps):
    __tablename__ = "foreshadowing_chapters"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    foreshadowing_id: Mapped[UUID] = fk_uuid()
    chapter_id: Mapped[UUID] = fk_uuid()  # ON DELETE RESTRICT — 설치 챕터 삭제 시 재지정 요청
    role: Mapped[str] = mapped_column(String(20))  # setup | payoff | hint


class ForeshadowingEvent(Base, Timestamps):
    __tablename__ = "foreshadowing_events"
    __table_args__ = {"schema": "insight"}

    foreshadowing_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = fk_uuid()


class ForeshadowingCharacter(Base, Timestamps):
    __tablename__ = "foreshadowing_characters"
    __table_args__ = {"schema": "insight"}

    foreshadowing_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    character_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = fk_uuid()
