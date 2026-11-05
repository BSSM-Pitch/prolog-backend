from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from app.core import errors
from app.core.deps import ProjectContext, require_project_role
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.insight.character_memory import assessment, discovery, service
from app.insight.character_memory.schemas import (
    CharacterAssessmentJob,
    CharacterAssessmentOverview,
    CharacterMemoryAnalysisResponse,
    DetailsResponse,
    DiscoveredCharacter,
    OverviewResponse,
    ResolveDiscoveredCharacter,
)

router = APIRouter(prefix="/projects", tags=["Character memory"])
ProjectId = Annotated[UUID, Path(alias="projectId")]
ManuscriptId = Annotated[UUID, Path(alias="manuscriptId")]
CharacterId = Annotated[UUID, Path(alias="characterId")]
AnalysisId = Annotated[UUID, Path(alias="analysisId")]
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
EditorCtx = Annotated[ProjectContext, Depends(require_project_role("editor"))]
BASE = "/{projectId}/manuscripts/{manuscriptId}"


@router.get(
    BASE + "/character-memory",
    summary="원고의 인물 탐색·기억 분석 상태를 조회한다",
    dependencies=[Viewer],
    response_model=Envelope[OverviewResponse],
    responses=raises(errors.ManuscriptNotFound),
)
async def overview(
    project_id: ProjectId, manuscript_id: ManuscriptId, session: Session
) -> dict[str, Any]:
    """진행 상태와 최근 완료본. 원문·모델 호출 캐시는 노출하지 않는다."""
    return ok(await service.overview(session, project_id, manuscript_id))


@router.post(
    BASE + "/character-memory",
    summary="원고에서 인물을 자동 등록하고 기억을 분석한다",
    status_code=202,
    response_model=Envelope[CharacterMemoryAnalysisResponse],
    responses=raises(errors.ManuscriptNotFound, errors.ManuscriptTooShort, errors.InvalidInput),
)
async def build(
    project_id: ProjectId, manuscript_id: ManuscriptId, session: Session, ctx: EditorCtx
) -> dict[str, Any]:
    """원고에서 새 인물을 발견·등록한 뒤 기억을 분석한다. 같은 작업은 재사용한다."""
    return ok(
        service.job_view(await service.request(session, project_id, manuscript_id, ctx.user.id))
    )


@router.post(
    BASE + "/character-memory/{analysisId}/retry",
    summary="중단된 인물 기억 분석을 이어서 실행한다",
    dependencies=[Editor],
    response_model=Envelope[CharacterMemoryAnalysisResponse],
    status_code=202,
    responses=raises(
        errors.ManuscriptNotFound,
        errors.CharacterMemoryNotFound,
        errors.InvalidInput,
        errors.InvalidStatusTransition,
    ),
)
async def retry(
    project_id: ProjectId, manuscript_id: ManuscriptId, analysis_id: AnalysisId, session: Session
) -> dict[str, Any]:
    """원고·인물이 같은 실패 작업만 저장된 탐색·기억 체크포인트에서 재개한다."""
    return ok(
        service.job_view(await service.retry(session, project_id, manuscript_id, analysis_id))
    )


@router.post(
    BASE + "/character-memory/{analysisId}/candidates/{candidateId}/resolve",
    summary="인물 후보를 등록·별칭 연결·제외로 확인한다",
    response_model=Envelope[DiscoveredCharacter],
    responses=raises(
        errors.ManuscriptNotFound,
        errors.CharacterMemoryNotFound,
        errors.CharacterNotFound,
        errors.InvalidInput,
    ),
)
async def resolve_candidate(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    analysis_id: AnalysisId,
    candidate_id: Annotated[str, Path(alias="candidateId", min_length=1, max_length=100)],
    body: ResolveDiscoveredCharacter,
    session: Session,
    ctx: EditorCtx,
) -> dict[str, Any]:
    """보류한 이름을 새 인물로 등록하거나 기존 인물의 별칭으로 연결한다."""
    return ok(
        await discovery.resolve(
            session,
            project_id,
            manuscript_id,
            analysis_id,
            candidate_id,
            body.action,
            body.character_id,
            ctx.user.id,
            body.name,
        )
    )


@router.get(
    BASE + "/characters/{characterId}/details",
    summary="인물의 원문 기록·반복 경향·상황 수치를 조회한다",
    dependencies=[Viewer],
    response_model=Envelope[DetailsResponse],
    responses=raises(
        errors.ManuscriptNotFound, errors.CharacterNotFound, errors.CharacterMemoryNotReady
    ),
)
async def details(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    character_id: CharacterId,
    session: Session,
    through_order: Annotated[int | None, Query(ge=0)] = None,
) -> dict[str, Any]:
    """원문 인용·반복 경향·반례·잠정 해석을 분리한다. 모델 호출 없이 저장된 결과를 읽는다."""
    return ok(
        await service.details(session, project_id, manuscript_id, character_id, through_order)
    )


@router.get(
    BASE + "/characters/{characterId}/assessment",
    summary="인물 평가 기준과 저장된 평가를 조회한다",
    dependencies=[Viewer],
    response_model=Envelope[CharacterAssessmentOverview],
    responses=raises(
        errors.ManuscriptNotFound, errors.CharacterNotFound, errors.CharacterMemoryNotReady
    ),
)
async def get_assessment(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    character_id: CharacterId,
    session: Session,
    through_order: Annotated[int | None, Query(ge=0)] = None,
) -> dict[str, Any]:
    """선택한 시점까지의 기억에 대응하는 평가 작업과 공통 행동 기준을 읽는다."""
    return ok(
        await assessment.overview(session, project_id, manuscript_id, character_id, through_order)
    )


@router.post(
    BASE + "/characters/{characterId}/assessment",
    summary="원문 근거로 인물의 성향·판단력·역량을 평가한다",
    status_code=202,
    response_model=Envelope[CharacterAssessmentJob],
    responses=raises(
        errors.ManuscriptNotFound,
        errors.CharacterNotFound,
        errors.CharacterMemoryNotReady,
        errors.InvalidInput,
    ),
)
async def build_assessment(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    character_id: CharacterId,
    session: Session,
    ctx: EditorCtx,
    through_order: Annotated[int | None, Query(ge=0)] = None,
) -> dict[str, Any]:
    """확실한 원문 기록을 표집해 비동기로 평가한다. 근거가 부족한 항목은 점수를 보류한다."""
    return ok(
        await assessment.request(
            session, project_id, manuscript_id, character_id, ctx.user.id, through_order
        )
    )
