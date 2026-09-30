from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.authoring.rex import extraction, service
from app.authoring.rex.schemas import (
    RuleExtractionConfirm,
    RuleExtractionEnvelope,
    RuleExtractionResponse,
    WorldRuleCreate,
    WorldRuleResponse,
    WorldRuleUpdate,
)
from app.core import errors
from app.core.deps import ProjectContext, require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, Page, ok, raises
from app.db.session import Session

router = APIRouter(prefix="/projects", tags=["REX"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
RuleId = Annotated[UUID, Path(alias="ruleId")]
ManuscriptId = Annotated[UUID, Path(alias="manuscriptId")]
ExtractionId = Annotated[UUID, Path(alias="extractionId")]
EditorCtx = Annotated[ProjectContext, Depends(require_project_role("editor"))]
# ASSUMPTION: 명세가 역할을 정하지 않았다. MSU·ASS 와 같이 읽기 viewer / 쓰기 editor.
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))

RULES = "/{projectId}/world-rules"
EXTRACTIONS = "/{projectId}/manuscripts/{manuscriptId}/rule-extractions"
EXTRACTION = EXTRACTIONS + "/{extractionId}"
ExtractionMissing = raises(errors.RuleExtractionNotFound)
RULE = RULES + "/{ruleId}"


@router.get(
    RULES,
    dependencies=[Viewer],
    summary="세계관 규칙 목록",
    response_model=Page[WorldRuleResponse],
)
async def list_rules(
    project_id: ProjectId, session: Session, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> dict[str, Any]:
    """먼저 만든 규칙부터. 커서 페이지네이션이다. SCDS 가 같은 목록을 판정 기준으로 쓴다."""
    rows = await service.list_rules(
        session, project_id, limit, decode_cursor(cursor) if cursor else None
    )
    page, meta = next_cursor(rows, limit)
    return ok(service.to_responses(page), meta)


@router.post(
    RULES,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Editor],
    summary="규칙을 직접 추가한다",
    response_model=Envelope[WorldRuleResponse],
)
async def create_rule(
    project_id: ProjectId, body: WorldRuleCreate, session: Session
) -> dict[str, Any]:
    """`origin` 은 `user_added`. `violation_keywords` 는 설정 충돌 검토가 위반 판정에 쓴다.

    같은 키워드를 두 번 보내면 하나로 합친다(순서 유지).
    """
    return ok(await service.create_rule(session, project_id, body))


@router.patch(
    RULE,
    dependencies=[Editor],
    summary="규칙을 수정한다",
    response_model=Envelope[WorldRuleResponse],
    responses=raises(errors.WorldRuleNotFound),
)
async def update_rule(
    project_id: ProjectId, rule_id: RuleId, body: WorldRuleUpdate, session: Session
) -> dict[str, Any]:
    """보낸 필드만 바뀐다. `violation_keywords` 는 배열 전체를 교체한다.

    AI 가 추출한 규칙도 고칠 수 있다. `origin`·`evidence` 는 바뀌지 않는다.
    """
    return ok(await service.update_rule(session, project_id, rule_id, body))


@router.delete(
    RULE,
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
    summary="규칙을 삭제한다",
    responses=raises(errors.WorldRuleNotFound),
)
async def delete_rule(project_id: ProjectId, rule_id: RuleId, session: Session) -> None:
    """되돌릴 수 없다. 삭제한 규칙은 설정 충돌 판정에서 바로 빠진다."""
    await service.delete_rule(session, project_id, rule_id)


# --- AI 추출(명세 §4.1~4.4) ------------------------------------------------


@router.post(
    EXTRACTIONS,
    status_code=status.HTTP_202_ACCEPTED,
    summary="원고에서 규칙 추출을 요청한다",
    response_model=Envelope[RuleExtractionResponse],
    responses=raises(errors.ManuscriptNotFound),
)
async def request_extraction(
    project_id: ProjectId, manuscript_id: ManuscriptId, session: Session, ctx: EditorCtx
) -> dict[str, Any]:
    """비동기다. `status` 는 `queued` 로 시작한다 — 조회로 폴링한다.

    본문이 준비된(`ready`) 원고만 된다. 업로드 추출 중이거나 빈 원고면 `INVALID_INPUT`.
    화면 21 의 "원고에서 다시 추출" 도 이 요청이다(새 추출).
    """
    job = await extraction.request(session, project_id, manuscript_id, ctx.user.id)
    return ok(extraction.to_response(job))


@router.get(
    EXTRACTION,
    dependencies=[Viewer],
    summary="규칙 추출 결과를 조회한다 (폴링)",
    response_model=RuleExtractionEnvelope,
    responses=ExtractionMissing,
)
async def get_extraction(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    extraction_id: ExtractionId,
    session: Session,
) -> dict[str, Any]:
    """`queued` → `extracting` → `completed` | `failed`.

    완료면 `extracted_rules` 에 후보가 오고, 후보마다 `review_status`(`pending` · `confirmed` ·
    `ignored`)가 있다. 규칙이 없으면 빈 배열이다(오류 아님). 실패면 200 에 `error` 가 함께 온다.
    """
    job = await extraction.get(session, project_id, manuscript_id, extraction_id)
    meta = (job.result or {}).get("meta") or {}
    body = ok(
        extraction.to_response(job),
        {"removed_evidence_count": meta.get("removed_evidence_count", 0)}
        if job.status == "completed"
        else {},
    )
    error = extraction.error_of(job)
    return body | ({"error": error} if error else {})


@router.post(
    EXTRACTION + "/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Editor],
    summary="규칙 추출을 다시 시도한다",
    response_model=Envelope[RuleExtractionResponse],
    responses=raises(errors.RuleExtractionNotFound, errors.ExtractionNotReady),
)
async def retry_extraction(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    extraction_id: ExtractionId,
    session: Session,
) -> dict[str, Any]:
    """`failed` 에서만 된다(아니면 `EXTRACTION_NOT_READY`).

    완료본을 다시 뽑으려면 새 추출을 요청한다.
    """
    job = await extraction.retry(session, project_id, manuscript_id, extraction_id)
    return ok(extraction.to_response(job))


@router.post(
    EXTRACTION + "/confirm",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Editor],
    summary="추출 후보를 규칙으로 확정한다",
    response_model=Envelope[list[WorldRuleResponse]],
    responses=raises(errors.RuleExtractionNotFound, errors.ExtractionNotReady),
)
async def confirm_extraction(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    extraction_id: ExtractionId,
    body: RuleExtractionConfirm,
    session: Session,
) -> dict[str, Any]:
    """`selected_indices` 의 후보를 `origin = ai_extracted` 규칙으로 만든다.

    새로 만든 규칙만 돌려준다.

    - `edits` 로 확정 전에 제목·설명·키워드를 고친다(키는 선택한 index).
    - `ignored_indices` 는 후보를 "무시" 로 표시한다(화면 21). 무시한 후보도 나중에 확정할 수 있다.
    - 이미 확정한 후보는 다시 만들지 않는다. 완료 전이면 `EXTRACTION_NOT_READY`.
    """
    rules = await extraction.confirm(session, project_id, manuscript_id, extraction_id, body)
    return ok(service.to_responses(rules))
