from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.core import errors
from app.core.deps import ProjectContext, require_project_role
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.insight.ssm import service
from app.insight.ssm.schemas import (
    AnalysisEnvelope,
    AnalysisResponse,
    NodeResponse,
    NodeUpdate,
    StructureMapResponse,
)

router = APIRouter(prefix="/projects", tags=["SSM"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
ManuscriptId = Annotated[UUID, Path(alias="manuscriptId")]
AnalysisId = Annotated[UUID, Path(alias="analysisId")]
NodeId = Annotated[UUID, Path(alias="nodeId")]
# ASSUMPTION: 명세가 역할을 정하지 않았다. 읽기 viewer / 쓰기 editor.
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
EditorCtx = Annotated[ProjectContext, Depends(require_project_role("editor"))]

BASE = "/{projectId}/manuscripts/{manuscriptId}"
ANALYSES = BASE + "/structure-analyses"
ANALYSIS = ANALYSES + "/{analysisId}"
NODE = BASE + "/structure-map/nodes/{nodeId}"


@router.post(
    ANALYSES,
    status_code=status.HTTP_202_ACCEPTED,
    summary="구조 분석을 요청한다",
    response_model=Envelope[AnalysisResponse],
    responses=raises(errors.ManuscriptNotFound, errors.ManuscriptTooShort),
)
async def request_analysis(
    project_id: ProjectId, manuscript_id: ManuscriptId, session: Session, ctx: EditorCtx
) -> dict[str, Any]:
    """비동기다(`queued`). 챕터마다 AI 를 부르므로 장편은 수십 분 걸린다 — 조회로 폴링한다.

    원고가 비어 있으면 `MANUSCRIPT_TOO_SHORT`(422). 화면 03 의 "다시 분석" 도 이 요청이다.
    """
    job = await service.request(session, project_id, manuscript_id, ctx.user.id)
    return ok(service.to_analysis(job))


@router.get(
    ANALYSIS,
    dependencies=[Viewer],
    summary="구조 분석 상태 (폴링)",
    response_model=AnalysisEnvelope,
    responses=raises(errors.StructureAnalysisNotFound),
)
async def get_analysis(
    project_id: ProjectId, manuscript_id: ManuscriptId, analysis_id: AnalysisId, session: Session
) -> dict[str, Any]:
    """`queued` → `analyzing` → `completed`(`structure_map_ref`) | `failed`(200 에 `error`).

    실패한 챕터 위치는 `error.details.chunk_index`·`chunk_count` 에 있다.
    """
    job = await service.get_analysis(session, project_id, manuscript_id, analysis_id)
    error = service.analysis_error(job)
    return ok(service.to_analysis(job)) | ({"error": error} if error else {})


@router.post(
    ANALYSIS + "/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Editor],
    summary="구조 분석을 다시 시도한다",
    response_model=Envelope[AnalysisResponse],
    responses=raises(errors.StructureAnalysisNotFound, errors.InvalidStatusTransition),
)
async def retry(
    project_id: ProjectId, manuscript_id: ManuscriptId, analysis_id: AnalysisId, session: Session
) -> dict[str, Any]:
    """`failed` 에서만(아니면 `INVALID_STATUS_TRANSITION`)."""
    job = await service.retry(session, project_id, manuscript_id, analysis_id)
    return ok(service.to_analysis(job))


@router.get(
    BASE + "/structure-map",
    dependencies=[Viewer],
    summary="최신 구조 지도",
    response_model=Envelope[StructureMapResponse],
    responses=raises(errors.StructureMapNotFound),
)
async def get_map(
    project_id: ProjectId, manuscript_id: ManuscriptId, session: Session
) -> dict[str, Any]:
    """막 · 노드(챕터 순) · 인과 간선. 분석이 끝난 적 없으면 `STRUCTURE_MAP_NOT_FOUND`.

    다시 분석하면 AI 노드는 바뀌고 사용자가 고친 노드(`is_user_edited`)는 남는다.
    """
    return ok(await service.get_map(session, project_id, manuscript_id))


@router.get(
    NODE,
    dependencies=[Viewer],
    summary="구조 지도 노드를 조회한다",
    response_model=Envelope[NodeResponse],
    responses=raises(errors.StructureMapNotFound, errors.StructureNodeNotFound),
)
async def get_node(
    project_id: ProjectId, manuscript_id: ManuscriptId, node_id: NodeId, session: Session
) -> dict[str, Any]:
    """사건 · 전환점 · 절정 하나."""
    return ok(await service.get_node(session, project_id, manuscript_id, node_id))


@router.patch(
    NODE,
    dependencies=[Editor],
    summary="구조 지도 노드를 고친다",
    response_model=Envelope[NodeResponse],
    responses=raises(errors.StructureMapNotFound, errors.StructureNodeNotFound),
)
async def update_node(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    node_id: NodeId,
    body: NodeUpdate,
    session: Session,
) -> dict[str, Any]:
    """제목 · 요약. 바뀌면 `is_user_edited = true` — 다시 분석해도 덮어쓰지 않는다."""
    return ok(await service.update_node(session, project_id, manuscript_id, node_id, body))
