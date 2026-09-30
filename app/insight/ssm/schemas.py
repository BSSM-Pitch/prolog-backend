"""SSM 명세 §2. 분석은 `structure_analysis` 잡이고, 지도는 원고마다 하나(최신)다."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.core.response import Envelope, ErrorBody

AnalysisStatus = Literal["queued", "analyzing", "completed", "failed"]
NodeType = Literal["event", "turning_point", "climax"]


class AnalysisResponse(BaseModel):
    analysis_id: UUID
    manuscript_id: UUID
    status: AnalysisStatus
    structure_map_ref: str | None = Field(description="완료되면 구조 지도 경로(명세 §4.2)")
    created_at: datetime
    updated_at: datetime


class AnalysisEnvelope(Envelope[AnalysisResponse]):
    """명세 §4.2 "실패" 는 200 에 `data`(status=failed) 와 `error` 를 함께 준다."""

    error: ErrorBody | None = Field(default=None, description="status=failed 일 때만 있다")


class Act(BaseModel):
    act_name: str
    chapter_from: int
    chapter_to: int
    summary: str


class Edge(BaseModel):
    from_node_id: UUID
    to_node_id: UUID
    relation: str


class NodeResponse(BaseModel):
    node_id: UUID
    type: NodeType
    chapter: int | None
    title: str
    summary: str | None
    character_ids: list[UUID]
    # 사용자가 고친 노드는 다시 분석해도 남는다(§12-2 결정). 명세에 없다.
    is_user_edited: bool


class StructureMapResponse(BaseModel):
    manuscript_id: UUID
    analysis_id: UUID | None
    acts: list[Act]
    nodes: list[NodeResponse]
    edges: list[Edge]
    generated_at: datetime


class NodeUpdate(BaseModel):
    title: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
        | None
    ) = None
    summary: Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)] | None = None
