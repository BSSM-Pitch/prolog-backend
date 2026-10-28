"""RCV: 사용자 기록. FTS처럼 쓰기는 chapter_id, 표시는 chapter 번호를 쓴다.

원고마다 장 번호가 겹치므로 history에 manuscript_id를 포함하고 시점 조회는 원고를 지정한다.
event_title은 연결된 사건 ID가 아니라 사용자가 입력한 메모(note)다.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

State = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class RelationshipEntryCreate(BaseModel):
    chapter_id: UUID
    state: State
    trust: int | None = Field(default=None, ge=0, le=100)
    event_title: str | None = Field(default=None, max_length=2000)


class RelationshipCreate(BaseModel):
    source_character_id: UUID
    target_character_id: UUID
    initial_history: RelationshipEntryCreate


class RelationshipHistoryEntry(BaseModel):
    chapter_id: UUID
    manuscript_id: UUID
    chapter: int
    state: str
    trust: int | None
    event_title: str | None
    event_id: UUID | None = None
    event_deleted: bool = False


class RelationshipResponse(BaseModel):
    relationship_id: UUID
    project_id: UUID
    source_character_id: UUID
    target_character_id: UUID
    history: list[RelationshipHistoryEntry]
    created_at: datetime
    updated_at: datetime


class RelationshipSnapshot(BaseModel):
    relationship_id: UUID
    requested_chapter: int
    resolved_chapter: int | None
    state: str | None
    trust: int | None
    linked_event: UUID | None = None
    is_carried_forward: bool


class RelationshipNode(BaseModel):
    character_id: UUID
    name: str
    role_label: str = ""


class RelationshipEdge(BaseModel):
    relationship_id: UUID
    source_character_id: UUID
    target_character_id: UUID
    state: str
    trust: int | None
    resolved_chapter: int
    is_carried_forward: bool


class RelationshipMindmap(BaseModel):
    chapter: int
    nodes: list[RelationshipNode]
    edges: list[RelationshipEdge]
