from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class CharacterDraft(Base, Timestamps):
    __tablename__ = "character_drafts"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    # NLCD forward 로 온 추출 작업. 사용자가 빈 초안을 직접 만들면 NULL.
    source_job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # ERD 에 없지만 화면의 자연어 원문을 담는다 (명세 수정 제안). 빈 초안은 NULL.
    source_text: Mapped[str | None] = mapped_column(Text)
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


class Character(Base, Timestamps):
    __tablename__ = "characters"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")
    role: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    created_from_draft_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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
    하나만 있어도 된다(DB CHECK 가 둘 다 NULL 인 행을 막는다). `item_id` 는 초안 단계에서
    draft_item, 확정 후에는 attribute 를 가리킨다(FK 없음 — 대상이 지워져도 이력은 남는다).
    """

    __tablename__ = "character_edit_histories"
    __table_args__ = {"schema": "authoring"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    character_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    draft_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    item_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    edited_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    phase: Mapped[str] = mapped_column(String(10))  # draft | confirmed
    action: Mapped[str] = mapped_column(String(10))  # create | update | delete
    # API 필드 이름 그대로(character_name · personality_tags · category 등)
    field: Mapped[str] = mapped_column(String(50))
    before_value: Mapped[str | None] = mapped_column(Text)
    after_value: Mapped[str | None] = mapped_column(Text)
