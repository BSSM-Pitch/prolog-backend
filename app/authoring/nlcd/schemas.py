"""NLCD 명세 §2. 추출 작업은 `ops.jobs` 행 하나다 — `extraction_id` 가 곧 `job_id` 다.

잡 status → 명세 status: `queued`·`running` → `analyzing`, `completed`, `failed`.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.core.response import Envelope, ErrorBody

Status = Literal["analyzing", "completed", "failed"]
SourceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ExtractionCreate(BaseModel):
    source_text: SourceText
    # 화면 22 의 "인물 이름". 명세에 없다(제안 목록). 추출은 이름을 뽑지 않으므로 사용자가 친다.
    # forward 때 초안 이름이 된다. 비우고 `target_character_id` 를 주면 그 캐릭터 이름을 쓴다.
    name: Name | None = None
    target_character_id: UUID | None = None


class ExtractedItem(BaseModel):
    value: str
    evidence: str


class ExtractedInfluenceItem(BaseModel):
    value: str
    type: str | None = None
    evidence: str


class ExtractionSummary(BaseModel):
    """목록용(명세 §4.4 "요약 필드만")."""

    extraction_id: UUID
    project_id: UUID
    source_text: str
    name: str | None
    target_character_id: UUID | None
    status: Status
    duplicate_of: UUID | None
    forwarded_draft_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ExtractionResponse(ExtractionSummary):
    """상세(명세 §4.2). 완료 전에는 네 목록이 비어 있다."""

    personality_tags: list[ExtractedItem] = []
    core_values: list[ExtractedItem] = []
    influence_relations: list[ExtractedInfluenceItem] = []
    emotion_keywords: list[ExtractedItem] = []


class ExtractionEnvelope(Envelope[ExtractionResponse]):
    """명세 §4.2 "실패" 는 200 에 `data`(status=failed) 와 `error` 를 **함께** 준다."""

    error: ErrorBody | None = Field(default=None, description="status=failed 일 때만 있다")


class ForwardResponse(BaseModel):
    extraction_id: UUID
    forwarded_draft_id: UUID


def items_of(result: dict[str, Any] | None) -> dict[str, list[Any]]:
    fields = ("personality_tags", "core_values", "influence_relations", "emotion_keywords")
    return {f: list((result or {}).get(f) or []) for f in fields}
