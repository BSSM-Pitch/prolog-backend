from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.authoring.rex import service
from app.authoring.rex.schemas import WorldRuleCreate, WorldRuleResponse, WorldRuleUpdate
from app.core import errors
from app.core.deps import require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, Page, ok, raises
from app.db.session import Session

router = APIRouter(prefix="/projects", tags=["REX"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
RuleId = Annotated[UUID, Path(alias="ruleId")]
# ASSUMPTION: 명세가 역할을 정하지 않았다. MSU·ASS 와 같이 읽기 viewer / 쓰기 editor.
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))

RULES = "/{projectId}/world-rules"
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
