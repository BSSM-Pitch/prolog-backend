"""AIQ 명세 §2. 답변은 assistant 메시지 하나이고, 그 메시지를 채우는 것이 `qa_answer` 잡이다."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.core.response import ErrorBody

Scope = Literal["whole", "selection"]
MessageStatus = Literal["pending", "completed", "failed"]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class SelectionRange(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ThreadCreate(BaseModel):
    scope: Scope = "whole"
    selection_range: SelectionRange | None = None
    question: Question


class MessageCreate(BaseModel):
    content: Question


class MessageResponse(BaseModel):
    message_id: UUID
    thread_id: UUID
    role: Literal["user", "assistant"]
    content: str | None = Field(description="assistant 답변은 pending 동안 null")
    status: MessageStatus
    # 화면 36 "응답 시간이 너무 길어져 중단됐어요" — 실패 사유. 명세에 없다(제안 목록).
    error: ErrorBody | None
    created_at: datetime


class ThreadResponse(BaseModel):
    thread_id: UUID
    manuscript_id: UUID
    scope: Scope
    selection_range: SelectionRange | None
    # 질문할 때 선택한 문장. 원고가 바뀌어도 무엇을 물었는지 남는다.
    selected_text: str | None
    title: str | None
    created_at: datetime
    updated_at: datetime


class ThreadSummary(ThreadResponse):
    """목록용 — 마지막 메시지 미리보기(명세 §4.1)."""

    last_message: MessageResponse | None


class ThreadDetail(BaseModel):
    thread: ThreadResponse
    messages: list[MessageResponse]
