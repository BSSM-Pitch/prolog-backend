"""SCDS 명세 §2. 사건 저장 → 룰 검출(동기) → AI 분석(잡) → 충돌 → 사용자 처리."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.core.response import Envelope, ErrorBody

CheckStatus = Literal["skipped", "queued", "analyzing", "completed", "failed"]
ConflictStatus = Literal["pending", "accepted", "ignored", "modified"]
Severity = Literal["low", "medium", "high"]
Content = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class EventCreate(BaseModel):
    character_ids: list[UUID] = Field(min_length=1, max_length=50)
    content: Content


class EventResponse(BaseModel):
    event_id: UUID
    chapter_id: UUID
    chapter: int
    character_ids: list[UUID]
    content: str
    created_at: datetime


class Candidate(BaseModel):
    rule_id: UUID
    character_id: UUID
    conflict_target: str
    matched_keyword: str


class RuleResult(BaseModel):
    has_candidate: bool
    skipped: bool
    skipped_reason: str | None
    candidates: list[Candidate]
    # 억제(ignored 처리한 같은 조합)로 뺀 후보 수. 명세에 없다(제안 목록).
    suppressed_count: int = 0


class ConflictCheckResponse(BaseModel):
    check_id: UUID
    event_id: UUID
    status: CheckStatus
    rule_result: RuleResult
    conflict_ids: list[UUID]
    created_at: datetime
    updated_at: datetime


class EventCreated(BaseModel):
    event: EventResponse
    conflict_check: ConflictCheckResponse


class ConflictCheckEnvelope(Envelope[ConflictCheckResponse]):
    """명세 §4.7 "실패" 는 200 에 `data`(status=failed) 와 `error` 를 함께 준다."""

    error: ErrorBody | None = Field(default=None, description="status=failed 일 때만 있다")


class ConflictResponse(BaseModel):
    conflict_id: UUID
    check_id: UUID | None
    event_id: UUID
    character_id: UUID | None
    rule_id: UUID | None
    chapter_id: UUID | None
    chapter: int | None
    conflict_target: str | None
    matched_keyword: str | None
    input_event: str
    # AI 가 끝내 실패하면 룰 후보만 남는다 — severity · advice 가 null 이다(명세 §4.7).
    detected_by: Literal["rule", "ai"]
    severity: Severity | None
    advice: str | None
    status: ConflictStatus
    modified_content: str | None
    created_at: datetime
    resolved_at: datetime | None


class ConflictAction(BaseModel):
    action: Literal["accepted", "ignored", "modified"]
    modified_content: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
        | None
    ) = None


class HistoryItem(BaseModel):
    conflict_id: UUID
    status: ConflictStatus
    resolved_at: datetime | None


def as_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
