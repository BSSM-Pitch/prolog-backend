"""REX 명세 §2.2 WorldRule. 이번 범위는 사용자가 직접 넣는 규칙(`user_added`)뿐이다.

`title` 은 명세에 없지만 화면 "21 · 설정 규칙" 이 모든 규칙을 "R01 · 붉은 빛과 기억" 처럼
제목과 설명 두 줄로 보여준다(제안 목록). `category` 는 화면이 쓰지 않아 API 에 내지 않는다.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.core.response import Envelope, ErrorBody

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Keywords = Annotated[list[Keyword], Field(max_length=50)]


def _dedupe(keywords: list[str] | None) -> list[str] | None:
    """순서를 지키며 중복을 없앤다. SCDS 가 키워드마다 한 번씩 판정하므로 중복은 의미가 없다."""
    return None if keywords is None else list(dict.fromkeys(keywords))


class WorldRuleCreate(BaseModel):
    title: Title
    description: Text
    violation_keywords: Keywords = []

    _unique = field_validator("violation_keywords")(_dedupe)


class WorldRuleUpdate(BaseModel):
    # 보낸 것만 바뀐다. 키워드는 배열 전체를 교체한다(화면이 칩 목록을 통째로 편집한다).
    title: Title | None = None
    description: Text | None = None
    violation_keywords: Keywords | None = None

    _unique = field_validator("violation_keywords")(_dedupe)


class WorldRuleResponse(BaseModel):
    rule_id: UUID
    project_id: UUID
    title: str
    description: str
    violation_keywords: list[str]
    origin: Literal["ai_extracted", "user_added"]
    # 명세는 `extraction_id`. AI 추출 경로(2b)에서 채운다 — 지금은 항상 null.
    extraction_id: UUID | None
    evidence: str | None
    source_chapter_no: int | None
    created_at: datetime
    updated_at: datetime


# --- AI 추출(명세 §2.1 · §4.1~4.4) ------------------------------------------

ExtractionStatus = Literal["queued", "extracting", "completed", "failed"]
ReviewStatus = Literal["pending", "confirmed", "ignored"]


class RuleCandidate(BaseModel):
    """추출 결과의 규칙 하나. `index` 로 확정·무시한다(명세 `selected_indices`)."""

    index: int
    title: str
    description: str
    violation_keywords: list[str]
    evidence: str | None
    # 패키지가 원고 텍스트만 받아 챕터를 알 수 없어 지금은 항상 null 이다.
    source_chapter: int | None
    # 화면 21 의 "검토 대기 / 확정 / 무시". 명세에 없다(제안 목록).
    review_status: ReviewStatus
    rule_id: UUID | None = Field(description="확정했으면 만든 규칙 id")


class RuleExtractionResponse(BaseModel):
    extraction_id: UUID
    manuscript_id: UUID
    status: ExtractionStatus
    extracted_rules: list[RuleCandidate]
    created_at: datetime
    updated_at: datetime


class RuleExtractionEnvelope(Envelope[RuleExtractionResponse]):
    """명세 §4.2 "실패" 는 200 에 `data`(status=failed) 와 `error` 를 함께 준다."""

    error: ErrorBody | None = Field(default=None, description="status=failed 일 때만 있다")


class CandidateEdit(BaseModel):
    title: Title | None = None
    description: Text | None = None
    violation_keywords: Keywords | None = None

    _unique = field_validator("violation_keywords")(_dedupe)


class RuleExtractionConfirm(BaseModel):
    """명세 §4.4 + `ignored_indices`(화면 21 "무시", 명세에 없다)."""

    selected_indices: list[int] = Field(default=[], max_length=200)
    # 키는 선택한 index(문자열). 명세 예시 `{"0": {"description": ...}}`.
    edits: dict[str, CandidateEdit] = {}
    ignored_indices: list[int] = Field(default=[], max_length=200)
