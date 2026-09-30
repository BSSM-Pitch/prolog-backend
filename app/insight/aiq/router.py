from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.core import errors
from app.core.deps import ProjectContext, require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, Page, ok, raises
from app.db.session import Session
from app.insight.aiq import service
from app.insight.aiq.schemas import (
    MessageCreate,
    MessageResponse,
    ThreadCreate,
    ThreadDetail,
    ThreadSummary,
)

router = APIRouter(prefix="/projects", tags=["AIQ"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
ManuscriptId = Annotated[UUID, Path(alias="manuscriptId")]
ThreadId = Annotated[UUID, Path(alias="threadId")]
MessageId = Annotated[UUID, Path(alias="messageId")]
# ASSUMPTION: 명세가 역할을 정하지 않았다. 읽기 viewer / 질문·삭제 editor.
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
EditorCtx = Annotated[ProjectContext, Depends(require_project_role("editor"))]

THREADS = "/{projectId}/manuscripts/{manuscriptId}/qa-threads"
THREAD = THREADS + "/{threadId}"
MESSAGE = THREAD + "/messages/{messageId}"
Missing = raises(errors.QaThreadNotFound)


@router.get(
    THREADS,
    dependencies=[Viewer],
    summary="질문 스레드 목록",
    response_model=Page[ThreadSummary],
    responses=raises(errors.ManuscriptNotFound),
)
async def list_threads(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """최근에 대화한 스레드부터. 스레드마다 마지막 메시지(`last_message`)가 온다."""
    rows = await service.list_threads(
        session, project_id, manuscript_id, limit, decode_cursor(cursor) if cursor else None
    )
    page, meta = next_cursor(rows, limit, key=lambda t: (t.updated_at, t.id))
    return ok(await service.to_summaries(session, page), meta)


@router.post(
    THREADS,
    status_code=status.HTTP_201_CREATED,
    summary="스레드를 만들고 첫 질문을 보낸다",
    response_model=Envelope[ThreadDetail],
    responses=raises(errors.ManuscriptNotFound, errors.InvalidSelectionRange),
)
async def create_thread(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    body: ThreadCreate,
    session: Session,
    ctx: EditorCtx,
) -> dict[str, Any]:
    """`scope`: `whole`(원고 전체, 기본) | `selection`(`selection_range` 필수, 문자 오프셋).

    응답의 두 번째 메시지(assistant)는 `pending` 이다 — 메시지 조회로 폴링한다. 범위가 원고를
    벗어나면 `INVALID_SELECTION_RANGE`. 선택한 문장은 `selected_text` 로 남아, 원고를 고쳐도
    그 문장을 다시 찾아 답한다(못 찾으면 답변이 `INVALID_SELECTION_RANGE` 로 실패).
    """
    return ok(await service.create_thread(session, project_id, manuscript_id, body, ctx.user.id))


@router.get(
    THREAD,
    dependencies=[Viewer],
    summary="스레드와 메시지 이력",
    response_model=Envelope[ThreadDetail],
    responses=Missing,
)
async def get_thread(
    project_id: ProjectId, manuscript_id: ManuscriptId, thread_id: ThreadId, session: Session
) -> dict[str, Any]:
    """메시지는 시간순, 전부."""
    return ok(await service.get_thread(session, project_id, manuscript_id, thread_id))


@router.post(
    THREAD + "/messages",
    status_code=status.HTTP_201_CREATED,
    summary="후속 질문을 보낸다",
    response_model=Envelope[list[MessageResponse]],
    responses=Missing,
)
async def follow_up(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    thread_id: ThreadId,
    body: MessageCreate,
    session: Session,
    ctx: EditorCtx,
) -> dict[str, Any]:
    """user 메시지와 `pending` assistant 메시지 두 개가 온다.

    앞선 대화(답이 있는 것)가 함께 AI 에 간다.
    """
    return ok(
        await service.follow_up(
            session, project_id, manuscript_id, thread_id, body.content, ctx.user.id
        )
    )


@router.get(
    MESSAGE,
    dependencies=[Viewer],
    summary="메시지(답변)를 조회한다 (폴링)",
    response_model=Envelope[MessageResponse],
    responses=raises(errors.QaThreadNotFound, errors.QaMessageNotFound),
)
async def get_message(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    thread_id: ThreadId,
    message_id: MessageId,
    session: Session,
) -> dict[str, Any]:
    """`pending` → `completed`(답변) | `failed`(`error` 에 사유 — 화면 36)."""
    return ok(await service.get_message(session, project_id, manuscript_id, thread_id, message_id))


@router.post(
    MESSAGE + "/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Editor],
    summary="답변을 다시 만든다",
    response_model=Envelope[MessageResponse],
    responses=raises(
        errors.QaThreadNotFound, errors.QaMessageNotFound, errors.InvalidStatusTransition
    ),
)
async def retry(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    thread_id: ThreadId,
    message_id: MessageId,
    session: Session,
) -> dict[str, Any]:
    """`failed` 인 assistant 메시지만. 그 밖이면 `INVALID_STATUS_TRANSITION`(명세 §4.6)."""
    return ok(await service.retry(session, project_id, manuscript_id, thread_id, message_id))


@router.delete(
    THREAD,
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
    summary="스레드를 삭제한다",
    responses=Missing,
)
async def delete_thread(
    project_id: ProjectId, manuscript_id: ManuscriptId, thread_id: ThreadId, session: Session
) -> None:
    """메시지도 함께 지워진다."""
    await service.delete_thread(session, project_id, manuscript_id, thread_id)
