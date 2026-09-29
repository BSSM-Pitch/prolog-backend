"""ASS 명세 §2 의 모양. DB 는 ERD 어휘(단수 category), API 는 명세 어휘(복수 field)다.

ERD: "DB 는 ASS 이름을 정본으로 쓴다" — 이름은 같고 단·복수만 다르다. 변환은 `FIELD_OF` 하나다.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

ItemField = Literal["personality_tags", "core_values", "influence_relations", "emotion_keywords"]
FIELD_OF: dict[str, ItemField] = {
    "personality_tag": "personality_tags",
    "core_value": "core_values",
    "influence_relation": "influence_relations",
    "emotion_keyword": "emotion_keywords",
}
CATEGORY_OF = {v: k for k, v in FIELD_OF.items()}

# 명세 §2.1 은 `pending_review` 다. DB 는 `pending`(0002 에서 유지, 제안 목록).
DraftStatus = Literal["pending_review", "confirmed", "discarded"]
STATUS_OUT: dict[str, DraftStatus] = {
    "pending": "pending_review",
    "confirmed": "confirmed",
    "discarded": "discarded",
}
STATUS_IN = {v: k for k, v in STATUS_OUT.items()}

Origin = Literal["ai_extracted", "user_added"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Value = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class DraftCreate(BaseModel):
    # 빈 초안. 항목은 POST .../items 로 채운다. 이름은 확정 때만 필수다.
    character_name: Name | None = None


class DraftUpdate(BaseModel):
    character_name: Name


class ItemCreate(BaseModel):
    field: ItemField
    value: Value


class ItemUpdate(BaseModel):
    # 화면의 "선택 항목 편집" 은 카테고리도 바꾼다. 보낸 것만 바뀐다.
    field: ItemField | None = None
    value: Value | None = None


class ItemResponse(BaseModel):
    item_id: UUID
    field: ItemField
    value: str
    # 화면의 "원문 근거". 사용자가 추가한 항목은 null.
    evidence: str | None
    origin: Origin


class DraftResponse(BaseModel):
    draft_id: UUID
    project_id: UUID
    character_name: str | None
    personality_tags: list[ItemResponse]
    core_values: list[ItemResponse]
    influence_relations: list[ItemResponse]
    emotion_keywords: list[ItemResponse]
    status: DraftStatus
    source_job_id: UUID | None
    confirmed_character_id: UUID | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConfirmRequest(BaseModel):
    # 비우면 신규 생성을 시도한다. 같은 이름이 있으면 409 DUPLICATE_CHARACTER_CANDIDATE.
    resolution: Literal["merge", "create_new"] | None = None
    merge_target_character_id: UUID | None = None


class AttributeResponse(BaseModel):
    attribute_id: UUID
    field: ItemField
    value: str
    evidence: str | None
    origin: Origin


class CharacterResponse(BaseModel):
    character_id: UUID
    project_id: UUID
    name: str
    personality_tags: list[AttributeResponse]
    core_values: list[AttributeResponse]
    influence_relations: list[AttributeResponse]
    emotion_keywords: list[AttributeResponse]
    status: Literal["confirmed"] = "confirmed"
    created_from_draft_id: UUID | None
    confirmed_at: datetime
    updated_at: datetime


class CharacterUpdate(BaseModel):
    name: Name


class EditHistoryEntry(BaseModel):
    history_id: UUID
    action: Literal["added", "modified", "removed"]
    field: str
    # 명세 §2.5 의 "바뀐 내용 요약". 삭제면 지워진 값, 아니면 바뀐 뒤 값.
    value: str | None
    before_value: str | None
    after_value: str | None
    # 초안 단계는 draft_item, 확정 후는 attribute. 이름 변경처럼 항목이 없으면 null.
    item_id: UUID | None
    edited_by: UUID | None
    at: datetime = Field(description="변경 시각")
