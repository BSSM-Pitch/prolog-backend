"""잡 최소 코어 — 생성 · 상태 전이 · 폴링 응답 (ROADMAP Phase 1).

**범위를 넘기지 않는다.** 재시도 · 좀비 회수 · DLQ · 멱등키는 Phase 2 다. 컬럼
(`attempt` · `max_attempt` · `idempotency_key`)은 이미 있지만 여기서 쓰지 않는다.

HTTP 표면도 없다. 잡을 노출하는 엔드포인트는 그 잡을 만드는 모듈(MSU·REX…)의 것이고,
에러 코드도 그쪽 명세에 있다 — 여기서 발명하지 않는다.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import settings
from app.jobs import repository as repo
from app.jobs.models import Job
from app.jobs.schemas import TERMINAL, JobResponse

# 최소 코어가 허용하는 전이. 재시도(failed → queued)는 Phase 2 에서 연다.
TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "skipped"}),
    "running": frozenset({"completed", "failed"}),
}


def to_response(job: Job) -> JobResponse:
    return JobResponse.model_validate(job, from_attributes=True)


def meta(job: Job) -> dict[str, Any]:
    """폴링 힌트. 끝난 잡에는 붙이지 않는다."""
    if job.status in TERMINAL:
        return {}
    return {"retry_after_ms": settings.job_retry_after_ms}


async def create(
    session: AsyncSession,
    *,
    project_id: UUID,
    job_type: str,
    target_type: str,
    queue: str,
    target_id: UUID | None = None,
    input: dict[str, Any] | None = None,
    created_by: UUID | None = None,
) -> Job:
    return await repo.add_job(
        session,
        Job(
            project_id=project_id,
            job_type=job_type,
            target_type=target_type,
            target_id=target_id,
            queue=queue,
            status="queued",
            input=input or {},
            created_by=created_by,
        ),
    )


async def transition(
    session: AsyncSession,
    job: Job,
    to: str,
    *,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> Job:
    """`completed` 는 `result`, `failed` 는 `error` 가 필수다 (DDL CHECK 가 강제한다)."""
    if to not in TRANSITIONS.get(job.status, frozenset()):
        raise errors.InvalidStatusTransition(f"{job.status} → {to} 는 허용되지 않는다")
    job.status = to
    if to == "running":
        job.started_at = datetime.now(UTC)
    if to in TERMINAL:
        job.finished_at = datetime.now(UTC)
    if result is not None:
        job.result = result
    if error is not None:
        job.error = error
    await session.flush()
    return job


async def get(session: AsyncSession, job_id: UUID) -> Job | None:
    return await repo.get_job(session, job_id)
