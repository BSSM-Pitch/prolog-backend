from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.authoring.nlcd import service
from app.authoring.nlcd.schemas import (
    ExtractionEnvelope,
    ExtractionResponse,
    ExtractionSummary,
    Status,
)
from app.core import errors
from app.core.deps import require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, Page, ok, raises
from app.db.session import Session

# 제출(POST)과 전달(forward)은 ASS 가 필요해 조합 레이어(app/api/nlcd.py)에 있다.
router = APIRouter(prefix="/projects", tags=["NLCD"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
ExtractionId = Annotated[UUID, Path(alias="extractionId")]
# ASSUMPTION: 명세가 역할을 정하지 않았다. 다른 모듈과 같이 읽기 viewer / 쓰기 editor.
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
ONE = "/{projectId}/nl-extractions/{extractionId}"


@router.get(
    "/{projectId}/nl-extractions",
    dependencies=[Viewer],
    summary="자연어 추출 이력",
    response_model=Page[ExtractionSummary],
)
async def list_extractions(
    project_id: ProjectId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    status_: Annotated[Status | None, Query(alias="status")] = None,
    target_character_id: UUID | None = None,
) -> dict[str, Any]:
    """최근 것부터. 요약 필드만 — 추출 항목은 상세 조회로 본다."""
    rows = await service.list_extractions(
        session,
        project_id,
        limit,
        decode_cursor(cursor) if cursor else None,
        status=status_,
        target_character_id=target_character_id,
    )
    page, meta = next_cursor(rows, limit)
    return ok([service.to_summary(j) for j in page], meta)


@router.get(
    ONE,
    dependencies=[Viewer],
    summary="추출 결과를 조회한다 (폴링)",
    response_model=ExtractionEnvelope,
    responses=raises(errors.ExtractionNotFound),
)
async def get_extraction(
    project_id: ProjectId, extraction_id: ExtractionId, session: Session
) -> dict[str, Any]:
    """`status = analyzing` 인 동안 폴링한다.

    - `completed`: 네 목록(성격 태그 · 핵심 가치 · 영향 관계 · 감정 키워드)에 항목과 근거가 온다.
      해당 내용이 없는 목록은 빈 배열이다(오류가 아니다). `meta.removed_evidence_count` 는 근거가
      원문에 없어 버린 항목 수다.
    - `failed`: 200 에 `data` 와 함께 `error` 가 온다
      (`AI_EXTRACTION_FAILED` · `AI_EXTRACTION_TIMEOUT` 등).
    """
    job = await service.get(session, project_id, extraction_id)
    body = ok(
        service.to_response(job),
        {"removed_evidence_count": (job.result or {}).get("removed_evidence_count", 0)}
        if job.status == "completed"
        else {},
    )
    error = service.error_of(job)
    return body | ({"error": error} if error else {})


@router.post(
    ONE + "/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Editor],
    summary="추출을 다시 시도한다",
    response_model=Envelope[ExtractionResponse],
    responses=raises(errors.ExtractionNotFound, errors.ExtractionNotReady),
)
async def retry(
    project_id: ProjectId, extraction_id: ExtractionId, session: Session
) -> dict[str, Any]:
    """`failed` 에서만 된다(아니면 `EXTRACTION_NOT_READY`). 자동 재시도를 다 쓴 추출도 된다."""
    return ok(service.to_response(await service.retry(session, project_id, extraction_id)))
