"""REX 명세 §2.2 WorldRule. 이번 범위는 사용자가 직접 넣는 규칙(`user_added`)뿐이다.

`title` 은 명세에 없지만 화면 "21 · 설정 규칙" 이 모든 규칙을 "R01 · 붉은 빛과 기억" 처럼
제목과 설명 두 줄로 보여준다(제안 목록). `category` 는 화면이 쓰지 않아 API 에 내지 않는다.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator

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
