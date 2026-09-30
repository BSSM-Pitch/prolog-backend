"""NLCD (Ring 3) — 자연어 서술에서 캐릭터 요소를 추출하는 **잡**.

AI 호출은 요청 안에서 하지 않는다. 제출하면 `ai` 큐에 잡을 걸고(outbox), `worker/ai_worker.py` 가
`job_handler.handle` 로 처리한다. 결과는 잡의 `result` 에, 실패 사유는 `error` 에 둔다.

제출 · 전달(forward)은 조합 레이어(`app/api/nlcd.py`)가 부른다 — 대상 캐릭터 확인과 초안 생성이
ASS 의 일이고, NLCD 와 ASS 는 서로를 부를 수 없다(규칙 3).
"""

import re
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.authoring.nlcd.schemas import (
    ExtractionResponse,
    ExtractionSummary,
    Status,
    items_of,
)
from app.core import errors
from app.jobs import service as jobs
from app.jobs.models import Job

JOB_TYPE = "nl_extraction"  # 0001 jobs_job_type_chk 의 이름
QUEUE = "ai"


def status_of(job: Job) -> Status:
    return cast(Status, {"completed": "completed", "failed": "failed"}.get(job.status, "analyzing"))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _summary(job: Job) -> dict[str, Any]:
    target = job.input.get("target_character_id")
    duplicate = job.input.get("duplicate_of")
    forwarded = (job.result or {}).get("forwarded_draft_id")
    return {
        "extraction_id": job.id,
        "project_id": job.project_id,
        "source_text": job.input["source_text"],
        "target_character_id": UUID(target) if target else None,
        "status": status_of(job),
        "duplicate_of": UUID(duplicate) if duplicate else None,
        "forwarded_draft_id": UUID(forwarded) if forwarded else None,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def to_summary(job: Job) -> ExtractionSummary:
    return ExtractionSummary.model_validate(_summary(job))


def to_response(job: Job) -> ExtractionResponse:
    return ExtractionResponse.model_validate(_summary(job) | items_of(job.result))


def error_of(job: Job) -> dict[str, Any] | None:
    """명세 §4.2 "실패": 잡 error 를 공통 에러 모양으로. 실패가 아니면 None."""
    if job.status != "failed" or not job.error:
        return None
    return {
        "code": job.error.get("code", "AI_EXTRACTION_FAILED"),
        "message": job.error.get("message", "AI 추출 처리 중 오류가 발생했습니다."),
        "details": job.error.get("details") or {},
    }


async def _job(
    session: AsyncSession, project_id: UUID, extraction_id: UUID, *, lock: bool = False
) -> Job:
    stmt = select(Job).where(
        Job.id == extraction_id, Job.project_id == project_id, Job.job_type == JOB_TYPE
    )
    if lock:
        stmt = stmt.with_for_update()
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise errors.ExtractionNotFound()
    return job


async def submit(
    session: AsyncSession,
    project_id: UUID,
    source_text: str,
    target_character_id: UUID | None,
    user_id: UUID,
) -> Job:
    """잡을 만들고 ai 큐에 알린다. 같은 문장(공백·대소문자 무시)을 전에 넣었으면 `duplicate_of`.

    중복이어도 막지 않는다(명세 §4.1 비고) — 병합은 전달·확정 단계에서 사용자가 정한다.
    """
    normalized = _normalize(source_text)
    earlier = (
        await session.execute(
            select(Job.id)
            .where(
                Job.project_id == project_id,
                Job.job_type == JOB_TYPE,
                Job.input["normalized"].astext == normalized,
            )
            .order_by(Job.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    job = await jobs.create(
        session,
        project_id=project_id,
        job_type=JOB_TYPE,
        target_type="character" if target_character_id else "project",
        target_id=target_character_id,
        queue=QUEUE,
        input={
            "source_text": source_text,
            "normalized": normalized,
            "target_character_id": str(target_character_id) if target_character_id else None,
            "duplicate_of": str(earlier) if earlier else None,
        },
        created_by=user_id,
    )
    jobs.announce(session, job)
    await session.flush()
    await session.refresh(job)
    return job


async def get(session: AsyncSession, project_id: UUID, extraction_id: UUID) -> Job:
    return await _job(session, project_id, extraction_id)


async def list_extractions(
    session: AsyncSession,
    project_id: UUID,
    limit: int,
    cursor: tuple[datetime, UUID] | None,
    *,
    status: Status | None = None,
    target_character_id: UUID | None = None,
) -> list[Job]:
    stmt = select(Job).where(Job.project_id == project_id, Job.job_type == JOB_TYPE)
    if status == "analyzing":
        stmt = stmt.where(Job.status.in_(("queued", "running")))
    elif status is not None:
        stmt = stmt.where(Job.status == status)
    if target_character_id is not None:
        stmt = stmt.where(Job.input["target_character_id"].astext == str(target_character_id))
    stmt = stmt.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit + 1)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Job.created_at, Job.id) < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def retry(session: AsyncSession, project_id: UUID, extraction_id: UUID) -> Job:
    """`failed` 에서만(명세 §4.3). 자동 재시도를 다 쓴 잡도 사용자는 되살린다."""
    job = await _job(session, project_id, extraction_id, lock=True)
    if job.status != "failed":
        raise errors.ExtractionNotReady(status=status_of(job))
    retried = await jobs.retry(session, job.id, by_user=True)
    assert retried is not None  # 행을 잠갔고 failed 다
    return retried


async def for_forward(session: AsyncSession, project_id: UUID, extraction_id: UUID) -> Job:
    """전달할 잡을 **잠가서** 돌려준다 — 두 번 눌러도 초안은 하나다."""
    job = await _job(session, project_id, extraction_id, lock=True)
    if job.status != "completed":
        raise errors.ExtractionNotReady(status=status_of(job))
    forwarded = (job.result or {}).get("forwarded_draft_id")
    if forwarded:
        raise errors.AlreadyForwarded(forwarded_draft_id=forwarded)
    return job


def mark_forwarded(job: Job, draft_id: UUID) -> None:
    job.result = {**(job.result or {}), "forwarded_draft_id": str(draft_id)}
