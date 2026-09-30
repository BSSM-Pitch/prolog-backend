from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.ai.client import AIClient, ai_client
from app.core import errors
from app.core.deps import ProjectContext, require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, Page, ok, raises
from app.db.session import Session
from app.insight.scds import service
from app.insight.scds.schemas import (
    ConflictAction,
    ConflictCheckEnvelope,
    ConflictCheckResponse,
    ConflictResponse,
    ConflictStatus,
    EventCreate,
    EventCreated,
    HistoryItem,
    Severity,
)

router = APIRouter(prefix="/projects", tags=["SCDS"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
ChapterId = Annotated[UUID, Path(alias="chapterId")]
CheckId = Annotated[UUID, Path(alias="checkId")]
ConflictId = Annotated[UUID, Path(alias="conflictId")]
AI = Annotated[AIClient, Depends(ai_client)]
# ASSUMPTION: 명세가 역할을 정하지 않았다. 읽기 viewer / 쓰기 editor.
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
EditorCtx = Annotated[ProjectContext, Depends(require_project_role("editor"))]


def _cursor(cursor: str | None) -> Any:
    return decode_cursor(cursor) if cursor else None


@router.post(
    "/{projectId}/chapters/{chapterId}/events",
    status_code=status.HTTP_201_CREATED,
    summary="사건을 저장하고 룰 기반 충돌 후보를 검출한다",
    response_model=Envelope[EventCreated],
    responses=raises(errors.ChapterNotFound, errors.CharacterNotFound, errors.RuleEngineError),
)
async def create_event(
    project_id: ProjectId,
    chapter_id: ChapterId,
    body: EventCreate,
    session: Session,
    ctx: EditorCtx,
    ai: AI,
) -> dict[str, Any]:
    """룰 검출은 **이 요청 안에서** 끝난다(AI 호출 없음). `conflict_check.status`:

    - `skipped`: 참조할 설정이 없거나(`rule_result.skipped = true`) 후보가 없다.
      AI 를 부르지 않는다.
    - `queued`: 후보가 있어 AI 분석 잡을 걸었다 — `GET .../conflict-checks/{checkId}` 로 폴링한다.

    전에 `ignored` 로 처리한 같은 조합(캐릭터 · 규칙 · 사건 문장)은 후보에서 빠진다
    (`rule_result.suppressed_count`). `character_ids` 는 이 프로젝트의 확정 캐릭터여야 한다.
    """
    return ok(await service.create_event(session, project_id, chapter_id, body, ctx.user.id, ai))


@router.get(
    "/{projectId}/conflict-checks/{checkId}",
    dependencies=[Viewer],
    summary="충돌 검사 상태 · 결과 (폴링)",
    response_model=ConflictCheckEnvelope,
    responses=raises(errors.ConflictCheckNotFound),
)
async def get_check(project_id: ProjectId, check_id: CheckId, session: Session) -> dict[str, Any]:
    """`queued` → `analyzing` → `completed`(`conflict_ids`) | `failed`.

    실패면 200 에 `error` 가 함께 오고, 룰 후보는 조언 없는 충돌로 남는다(명세 §4.7).
    """
    job = await service.get_check(session, project_id, check_id)
    body = ok(await service.to_check(session, job))
    error = service.check_error(job)
    return body | ({"error": error} if error else {})


@router.post(
    "/{projectId}/conflict-checks/{checkId}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Editor],
    summary="AI 분석을 다시 시도한다",
    response_model=Envelope[ConflictCheckResponse],
    responses=raises(errors.ConflictCheckNotFound, errors.InvalidStatusTransition),
)
async def retry_check(project_id: ProjectId, check_id: CheckId, session: Session) -> dict[str, Any]:
    """`failed` 에서만(아니면 `INVALID_STATUS_TRANSITION`). 룰 후보로 남긴 미처리 충돌은 지우고
    같은 후보로 AI 분석만 다시 한다."""
    job = await service.retry_check(session, project_id, check_id)
    return ok(await service.to_check(session, job))


@router.get(
    "/{projectId}/conflicts",
    dependencies=[Viewer],
    summary="충돌(조언) 목록",
    response_model=Page[ConflictResponse],
)
async def list_conflicts(
    project_id: ProjectId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    status_: Annotated[ConflictStatus | None, Query(alias="status")] = None,
    chapter_id: UUID | None = None,
    character_id: UUID | None = None,
    severity: Severity | None = None,
) -> dict[str, Any]:
    """최근 것부터. AI 디렉터 패널 · 화면 05 "미검토만" 은 `status=pending`."""
    rows = await service.list_conflicts(
        session,
        project_id,
        limit,
        _cursor(cursor),
        status=status_,
        chapter_id=chapter_id,
        character_id=character_id,
        severity=severity,
    )
    page, meta = next_cursor(rows, limit)
    return ok(await service.to_conflicts(session, page), meta)


@router.get(
    "/{projectId}/conflicts/history",
    dependencies=[Viewer],
    summary="처리한 충돌 이력",
    response_model=Page[HistoryItem],
)
async def history(
    project_id: ProjectId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    character_id: UUID | None = None,
    chapter_from: Annotated[int | None, Query(ge=1)] = None,
    chapter_to: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    """`pending` 이 아닌 충돌. 최근 것부터."""
    rows = await service.history(
        session,
        project_id,
        limit,
        _cursor(cursor),
        character_id=character_id,
        chapter_from=chapter_from,
        chapter_to=chapter_to,
    )
    page, meta = next_cursor(rows, limit)
    return ok(service.to_history(page), meta)


@router.get(
    "/{projectId}/conflicts/{conflictId}",
    dependencies=[Viewer],
    summary="충돌을 조회한다",
    response_model=Envelope[ConflictResponse],
    responses=raises(errors.ConflictNotFound),
)
async def get_conflict(
    project_id: ProjectId, conflict_id: ConflictId, session: Session
) -> dict[str, Any]:
    """`detected_by = rule` 이면 AI 분석이 실패해 조언 없이 남은 후보다."""
    return ok(await service.get_conflict(session, project_id, conflict_id))


@router.patch(
    "/{projectId}/conflicts/{conflictId}",
    summary="충돌을 처리한다 (수용 · 무시 · 수정)",
    response_model=Envelope[ConflictResponse],
    responses=raises(errors.ConflictNotFound, errors.InvalidStatusTransition),
)
async def resolve(
    project_id: ProjectId,
    conflict_id: ConflictId,
    body: ConflictAction,
    session: Session,
    ctx: EditorCtx,
) -> dict[str, Any]:
    """화면 05: 제안 수용 = `accepted` · 무시 = `ignored` · 직접 수정 = `modified`
    (`modified_content` 필수).

    한 번만 처리한다(아니면 `INVALID_STATUS_TRANSITION`). `ignored` 는 같은 조합이 다시
    후보가 되지 않게 억제한다.
    """
    return ok(await service.resolve(session, project_id, conflict_id, body, ctx.user.id))
