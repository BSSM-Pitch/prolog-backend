"""FTS 명세 §2 의 모양.

**챕터는 id 로 받는다.** 명세는 챕터 번호(integer)지만, 한 프로젝트에 원고가 여럿이면 번호가 겹친다
(`chapters` 는 원고마다 1 부터다). 응답은 명세대로 번호(`setup_chapter` 등)를 주고, 식별용
`chapters: [{chapter_id, chapter_no, role}]` 를 덧붙인다(제안 목록).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.core.response import Page, PageMeta

Status = Literal["unresolved", "resolved", "orphaned"]
Role = Literal["setup", "linked", "payoff"]
Priority = Literal["low", "medium", "high"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ForeshadowingCreate(BaseModel):
    title: Title
    description: str | None = Field(default=None, max_length=5000)
    setup_chapter_id: UUID
    linked_character_ids: list[UUID] = Field(default=[], max_length=50)


class ForeshadowingUpdate(BaseModel):
    # payoff·status 는 전용 API(PUT/DELETE .../payoff) 로만 바뀐다(명세 §4.4).
    title: Title | None = None
    description: str | None = Field(default=None, max_length=5000)
    # 설치 챕터 재지정 — 챕터가 삭제되어 orphaned 가 된 복선을 되살리는 길이다(명세 12항).
    setup_chapter_id: UUID | None = None


class ChapterBody(BaseModel):
    chapter_id: UUID


class PayoffBody(BaseModel):
    payoff_chapter_id: UUID


class LinkBody(BaseModel):
    # 명세는 `event | character`. 사건은 SCDS 가 생긴 뒤다(보고 "일부러 안 만든 것").
    target_type: Literal["character"]
    target_id: UUID


class ChapterRef(BaseModel):
    chapter_id: UUID
    # 챕터가 막 지워져 이벤트가 아직 처리되지 않았으면 null 이다.
    chapter_no: int | None
    role: Role


class ForeshadowingResponse(BaseModel):
    foreshadowing_id: UUID
    project_id: UUID
    title: str
    description: str | None
    setup_chapter: int | None  # orphaned 면 null
    linked_chapters: list[int]
    payoff_chapter: int | None
    status: Status
    linked_event_ids: list[UUID]
    linked_character_ids: list[UUID]
    chapters: list[ChapterRef]
    created_at: datetime
    updated_at: datetime


class SimilarCandidate(BaseModel):
    foreshadowing_id: UUID
    title: str


class UnresolvedItem(BaseModel):
    foreshadowing_id: UUID
    title: str
    setup_chapter: int
    elapsed_chapters: int


class UnresolvedResponse(BaseModel):
    current_chapter: int
    unresolved: list[UnresolvedItem]


class Advisory(BaseModel):
    foreshadowing_id: UUID
    message: str
    setup_chapter: int
    latest_linked_chapter: int | None
    priority: Priority


class Marker(BaseModel):
    chapter: int
    type: Role


class TimelineTrack(BaseModel):
    foreshadowing_id: UUID
    title: str
    status: Status
    markers: list[Marker]
    is_open: bool


class ChapterForeshadowing(BaseModel):
    foreshadowing_id: UUID
    title: str
    role: Role


class StatusCounts(BaseModel):
    """화면 04 의 "미회수 3건 · 회수 완료 5건". 명세에 없다(제안 목록)."""

    unresolved: int
    resolved: int
    orphaned: int


class ForeshadowingPageMeta(PageMeta):
    status_counts: StatusCounts = Field(
        description="필터와 무관한 이 프로젝트의 상태별 복선 수(현황 카드용)"
    )


class ForeshadowingPage(Page[ForeshadowingResponse]):
    meta: ForeshadowingPageMeta
