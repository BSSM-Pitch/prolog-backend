from uuid import UUID

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class Character(Base, Timestamps):
    __tablename__ = "characters"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")
    role: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)


class CharacterAttribute(Base, Timestamps):
    """CharacterPersonalityTags + CharacterCoreValues 를 category 로 통합한 테이블(§5).

    `character_draft_items` 와 모양이 같다 — 확정 시 그대로 승계하기 위해서다.
    """

    __tablename__ = "character_attributes"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    character_id: Mapped[UUID] = fk_uuid()
    # personality_tag | core_value | influence_relation | emotion_keyword (NLCD 가 뽑는 4종)
    category: Mapped[str] = mapped_column(String(30))
    value: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)  # 원문 근거 문장
    origin: Mapped[str] = mapped_column(String(20))  # ai_extracted | user_added


class CharacterEditHistory(Base, Timestamps):
    """append-only. 확정 전(draft) 과 확정 후(confirmed) 이력을 한 테이블에 담는다.

    화면이 초안 단계에서도 항목별 편집 이력을 보여주므로 `character_id` 와 `draft_id` 중
    하나만 있어도 된다(DB CHECK 가 둘 다 NULL 인 행을 막는다).
    """

    __tablename__ = "character_edit_histories"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    character_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    draft_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    edited_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    phase: Mapped[str] = mapped_column(String(10))  # draft | confirmed
    action: Mapped[str] = mapped_column(String(10))  # create | update | delete
    field: Mapped[str] = mapped_column(String(50))  # name · personality_tag 등
    before_value: Mapped[str | None] = mapped_column(Text)
    after_value: Mapped[str | None] = mapped_column(Text)
