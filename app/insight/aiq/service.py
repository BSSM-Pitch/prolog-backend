"""AIQ (Ring 4) — 원고에 대한 질문 스레드. 답변은 `qa_answer` 잡이 채운다.

질문 하나 = user 메시지(`completed`) + assistant 메시지(`pending`, content 없음) + 잡 하나.
잡이 끝나면 assistant 메시지가 `completed`(답변) 또는 `failed` 가 된다. 자동 재시도 중에는
`pending` 그대로다. 사용자 재시도는 `failed` 에서만(명세 §4.6, 아니면 `INVALID_STATUS_TRANSITION`).

**선택 구간(§12-4).** 문자 오프셋은 원고를 고치면 어긋난다. 질문할 때 선택한 문장을
`selected_text` 로 남기고, 답을 만들 때 그 자리의 문장이 다르면 현재 원고에서 다시 찾는다.
못 찾으면 재시도 없이 `INVALID_SELECTION_RANGE` 로 실패한다 — 다른 문장에 답하지 않는다.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.client import AIClient, Result
from app.content.manuscripts import service as manuscripts
from app.core import errors
from app.core.response import ErrorBody
from app.insight.aiq.models import QaMessage, QaThread
from app.insight.aiq.schemas import (
    MessageResponse,
    SelectionRange,
    ThreadCreate,
    ThreadDetail,
    ThreadResponse,
    ThreadSummary,
)
from app.jobs import service as jobs
from app.jobs.models import Job

JOB_TYPE = "qa_answer"  # 0001 jobs_job_type_chk 의 이름
QUEUE = "ai"
TITLE_CHARS = 50
PACKAGE_SCOPE = {"whole": "project", "selection": "selection"}  # 0009 머리말


# --- 응답 ---------------------------------------------------------------------


async def _errors_of(session: AsyncSession, rows: Sequence[QaMessage]) -> dict[UUID, Any]:
    ids = [m.job_id for m in rows if m.status == "failed" and m.job_id]
    if not ids:
        return {}
    found = await session.execute(select(Job.id, Job.error).where(Job.id.in_(ids)))
    return {i: e for i, e in found if e}


async def to_messages(session: AsyncSession, rows: Sequence[QaMessage]) -> list[MessageResponse]:
    failures = await _errors_of(session, rows)
    out = []
    for m in rows:
        error = failures.get(m.job_id) if m.job_id else None
        out.append(
            MessageResponse(
                message_id=m.id,
                thread_id=m.thread_id,
                role=cast(Any, m.role),
                content=m.content,
                status=cast(Any, m.status or "completed"),
                error=(
                    ErrorBody(
                        code=error.get("code", "AI_RESPONSE_FAILED"),
                        message=error.get("message", "AI 응답 생성에 실패했습니다."),
                        details=error.get("details") or {},
                    )
                    if error
                    else None
                ),
                created_at=m.created_at,
            )
        )
    return out


def to_thread(t: QaThread) -> ThreadResponse:
    selection = (
        SelectionRange(start=t.selection_start, end=t.selection_end)
        if t.selection_start is not None and t.selection_end is not None
        else None
    )
    return ThreadResponse(
        thread_id=t.id,
        manuscript_id=cast(UUID, t.manuscript_id),
        scope=cast(Any, t.scope),
        selection_range=selection,
        selected_text=t.selected_text,
        title=t.title,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


async def _messages(session: AsyncSession, thread_id: UUID) -> list[QaMessage]:
    stmt = (
        select(QaMessage)
        .where(QaMessage.thread_id == thread_id)
        .order_by(QaMessage.created_at, QaMessage.id)
    )
    return list((await session.execute(stmt)).scalars())


async def detail(session: AsyncSession, thread: QaThread) -> ThreadDetail:
    rows = await _messages(session, thread.id)
    return ThreadDetail(thread=to_thread(thread), messages=await to_messages(session, rows))


# --- 조회 헬퍼 -----------------------------------------------------------------


async def _thread(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, thread_id: UUID
) -> QaThread:
    stmt = select(QaThread).where(
        QaThread.id == thread_id,
        QaThread.project_id == project_id,
        QaThread.manuscript_id == manuscript_id,
    )
    thread = (await session.execute(stmt)).scalar_one_or_none()
    if thread is None:
        raise errors.QaThreadNotFound()
    return thread


async def _message(
    session: AsyncSession, thread: QaThread, message_id: UUID, *, lock: bool = False
) -> QaMessage:
    stmt = select(QaMessage).where(QaMessage.id == message_id, QaMessage.thread_id == thread.id)
    if lock:
        stmt = stmt.with_for_update()
    message = (await session.execute(stmt)).scalar_one_or_none()
    if message is None:
        raise errors.QaMessageNotFound()
    return message


# --- 질문 ---------------------------------------------------------------------


async def _ask(
    session: AsyncSession, thread: QaThread, question: str, user_id: UUID
) -> list[QaMessage]:
    """user 메시지 + pending assistant 메시지 + 잡. 둘은 순서가 보장되게 시각을 따로 찍는다."""
    asked = QaMessage(
        project_id=thread.project_id,
        thread_id=thread.id,
        role="user",
        content=question,
        status="completed",
        created_at=func.clock_timestamp(),
    )
    answer = QaMessage(
        project_id=thread.project_id,
        thread_id=thread.id,
        role="assistant",
        status="pending",
        created_at=func.clock_timestamp(),
    )
    session.add_all([asked, answer])
    await session.flush()
    job = await jobs.create(
        session,
        project_id=thread.project_id,
        job_type=JOB_TYPE,
        target_type="qa_message",
        target_id=answer.id,
        queue=QUEUE,
        input={"question_message_id": str(asked.id)},
        created_by=user_id,
    )
    answer.job_id = job.id
    jobs.announce(session, job)
    await session.flush()
    for m in (asked, answer):
        await session.refresh(m)
    return [asked, answer]


async def create_thread(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    body: ThreadCreate,
    user_id: UUID,
) -> ThreadDetail:
    manuscript = await manuscripts.get(session, project_id, manuscript_id)  # 없으면 404
    text = manuscript.content or ""
    if not text.strip():
        raise errors.InvalidInput("본문이 있는 원고에만 질문할 수 있습니다", field="manuscript_id")
    selected: str | None = None
    if body.scope == "selection":
        r = body.selection_range
        if r is None or not 0 <= r.start < r.end <= len(text):
            raise errors.InvalidSelectionRange(
                selection_range=r.model_dump() if r else None, length=len(text)
            )
        selected = text[r.start : r.end]
    thread = QaThread(
        project_id=project_id,
        manuscript_id=manuscript_id,
        scope=body.scope,
        selection_start=body.selection_range.start
        if selected is not None and body.selection_range
        else None,
        selection_end=body.selection_range.end
        if selected is not None and body.selection_range
        else None,
        selected_text=selected,
        # 명세 §2.1 "첫 질문으로 자동 생성 가능"
        title=body.question[:TITLE_CHARS],
        created_by=user_id,
    )
    session.add(thread)
    await session.flush()
    rows = await _ask(session, thread, body.question, user_id)
    await session.refresh(thread)
    return ThreadDetail(thread=to_thread(thread), messages=await to_messages(session, rows))


async def follow_up(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    thread_id: UUID,
    question: str,
    user_id: UUID,
) -> list[MessageResponse]:
    thread = await _thread(session, project_id, manuscript_id, thread_id)
    thread.updated_at = func.now()
    return await to_messages(session, await _ask(session, thread, question, user_id))


async def list_threads(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    limit: int,
    cursor: tuple[datetime, UUID] | None,
) -> list[QaThread]:
    """최근에 대화한 스레드부터(화면 02 "최근 질문")."""
    await manuscripts.get(session, project_id, manuscript_id)
    stmt = (
        select(QaThread)
        .where(QaThread.project_id == project_id, QaThread.manuscript_id == manuscript_id)
        .order_by(QaThread.updated_at.desc(), QaThread.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(
            tuple_(QaThread.updated_at, QaThread.id)
            < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def to_summaries(session: AsyncSession, threads: Sequence[QaThread]) -> list[ThreadSummary]:
    ids = [t.id for t in threads]
    last: dict[UUID, QaMessage] = {}
    if ids:
        stmt = (
            select(QaMessage)
            .where(QaMessage.thread_id.in_(ids))
            .distinct(QaMessage.thread_id)
            .order_by(QaMessage.thread_id, QaMessage.created_at.desc(), QaMessage.id.desc())
        )
        last = {m.thread_id: m for m in (await session.execute(stmt)).scalars()}
    previews = {m.thread_id: m for m in await to_messages(session, list(last.values()))}
    return [
        ThreadSummary(**to_thread(t).model_dump(), last_message=previews.get(t.id)) for t in threads
    ]


async def get_thread(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, thread_id: UUID
) -> ThreadDetail:
    return await detail(session, await _thread(session, project_id, manuscript_id, thread_id))


async def get_message(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, thread_id: UUID, message_id: UUID
) -> MessageResponse:
    thread = await _thread(session, project_id, manuscript_id, thread_id)
    return (await to_messages(session, [await _message(session, thread, message_id)]))[0]


async def retry(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, thread_id: UUID, message_id: UUID
) -> MessageResponse:
    """같은 질문으로 답변을 다시 만든다.

    화면 36 "질문은 저장돼 있으니 다시 시도하면 같은 질문으로 답변을 만들어요".
    """
    thread = await _thread(session, project_id, manuscript_id, thread_id)
    message = await _message(session, thread, message_id, lock=True)
    if message.role != "assistant" or message.status != "failed" or message.job_id is None:
        raise errors.InvalidStatusTransition(status=message.status, role=message.role)
    await jobs.retry(session, message.job_id, by_user=True)
    message.status = "pending"
    await session.flush()
    await session.refresh(message)
    return (await to_messages(session, [message]))[0]


async def delete_thread(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, thread_id: UUID
) -> None:
    """메시지는 CASCADE. 진행 중인 답변 잡은 결과를 쓸 메시지가 없어 조용히 끝난다."""
    await session.delete(await _thread(session, project_id, manuscript_id, thread_id))
    await session.flush()


# --- 워커 --------------------------------------------------------------------


def locate(text: str, start: int, end: int, selected: str) -> tuple[int, int] | None:
    """선택한 문장의 현재 위치. 제자리면 그대로, 옮겨졌으면 가장 가까운 곳, 사라졌으면 None."""
    if text[start:end] == selected:
        return start, end
    hits, at = [], text.find(selected)
    while at != -1:
        hits.append(at)
        at = text.find(selected, at + 1)
    if not hits:
        return None
    best = min(hits, key=lambda i: abs(i - start))
    return best, best + len(selected)


async def _answer_of(session: AsyncSession, job: Job) -> QaMessage:
    message = await session.get(QaMessage, cast(UUID, job.target_id))
    if message is None:
        raise errors.QaMessageNotFound()  # 스레드가 지워졌다
    return message


async def _prepare(session: AsyncSession, job: Job) -> dict[str, Any]:
    answer = await _answer_of(session, job)
    thread = await session.get(QaThread, answer.thread_id)
    assert thread is not None and thread.manuscript_id is not None
    manuscript = await manuscripts.get(session, thread.project_id, thread.manuscript_id)
    text = manuscript.content or ""
    question = await session.get(QaMessage, UUID(str(job.input["question_message_id"])))
    if question is None or not question.content:
        raise errors.QaMessageNotFound()
    # 이 질문 앞의 대화만, 답이 없는 pending · failed 는 빼고(패키지 README)
    history = [
        {"role": m.role, "content": m.content}
        for m in await _messages(session, thread.id)
        if m.created_at < question.created_at and m.status == "completed" and m.content
    ]
    selection = None
    if thread.scope == "selection":
        assert thread.selection_start is not None and thread.selection_end is not None
        spot = locate(
            text, thread.selection_start, thread.selection_end, thread.selected_text or ""
        )
        if spot is None:
            raise errors.InvalidSelectionRange(
                "원고가 바뀌어 선택한 문장을 찾을 수 없습니다", selected_text=thread.selected_text
            )
        selection = {"start": spot[0], "end": spot[1]}
    return {
        "question": question.content,
        "manuscript_text": text,
        "scope": PACKAGE_SCOPE.get(thread.scope, "project"),
        "selection_range": selection,
        "messages": history,
    }


def _call(ai: AIClient, args: Any) -> Result:
    return ai.run_aiq(
        args["question"],
        args["manuscript_text"],
        args["scope"],
        args["selection_range"],
        args["messages"],
    )


async def _answered(session: AsyncSession, job: Job, data: dict[str, Any]) -> None:
    answer = await session.get(QaMessage, cast(UUID, job.target_id))
    if answer is not None:
        answer.content, answer.status = str(data["content"]), "completed"


async def mark_failed(session: AsyncSession, job: Job, error: dict[str, Any] | None = None) -> None:
    """답변을 끝내 못 만들었을 때. 스위퍼(좀비 소진)도 이걸 부른다."""
    answer = await session.get(QaMessage, cast(UUID, job.target_id))
    if answer is not None:
        answer.status = "failed"


async def handle(sessions: Any, job_id: UUID, ai: AIClient) -> ai_jobs.Outcome:
    return await ai_jobs.run(
        sessions,
        job_id,
        JOB_TYPE,
        ai,
        prepare=_prepare,
        call=_call,
        on_success=_answered,
        on_failure=mark_failed,
    )
