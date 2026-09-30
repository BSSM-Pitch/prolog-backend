from uuid import UUID

from sqlalchemy import String, Text
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
    # unresolved | resolved | orphaned — 챕터 행에서 도출해 쓴다(`service._restatus`).
    # 챕터 번호 캐시는 두지 않는다(0006): 번호는 MSU 에서 바뀐다.
    status: Mapped[str] = mapped_column(String(20), default="unresolved")


class ForeshadowingChapter(Base, Timestamps):
    __tablename__ = "foreshadowing_chapters"
    __table_args__ = {"schema": "insight"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    foreshadowing_id: Mapped[UUID] = fk_uuid()
    # FK 없음(0006). 챕터 삭제는 `chapter.deleted` 이벤트로 받는다 — `fts.events`.
    chapter_id: Mapped[UUID] = fk_uuid()
    role: Mapped[str] = mapped_column(String(20))  # setup | payoff | linked


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
