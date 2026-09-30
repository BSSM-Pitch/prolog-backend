"""잡 코어 — 생성 · 선점 · 완료 · 재시도 · 좀비 회수 (ROADMAP Phase 2a).

**경합하는 전이는 전부 조건부 UPDATE 다** (아키텍처 설계서 §4.4). 분산 락 없이 DB 가 판정한다.

- 선점: `queued → running` 은 `WHERE status='queued'` 로 한 워커만 이긴다. 이긴 쪽이
  `attempt` 를 1 올리고, 그 값이 **이번 실행의 표식**이 된다.
- 완료·실패: `WHERE status='running' AND attempt=:표식`. 스위퍼가 먼저 좀비로 내렸거나
  (status 가 다르다) 이미 재시도되어 다른 워커가 다시 선점했으면(attempt 가 다르다) 0 행이다.
  늦게 돌아온 워커는 자기 결과를 **버린다** — 호출자가 롤백한다.
- 재시도: `failed → queued`, `attempt < max_attempt` 일 때만. 다 쓰면 `failed` 로 남고
  운영 알람(ERROR 로그)을 낸다. 잡의 상태 원천은 DB 이므로 잡을 DLQ 메시지로 옮기지 않는다.

자동 재시도는 **인프라 실패만** 받는다(좀비 · 스토리지 오류). 파일이 잘못된 도메인 실패는
다시 해도 같으므로 즉시 끝낸다. 사용자가 누르는 `/retry` 는 Phase 2b(REX)에서
이 `retry` 위에 얹는다.

HTTP 표면은 없다. 잡을 노출하는 엔드포인트는 그 잡을 만드는 모듈(MSU·REX…)의 것이다.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import settings
from app.events.outbox import emit
from app.jobs import repository as repo
from app.jobs.models import Job
from app.jobs.schemas import TERMINAL, JobResponse

log = logging.getLogger(__name__)

# 경합하지 않는 전이(생성 직후 skipped 등)만 transition() 이 다룬다.
TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "skipped"}),
    "running": frozenset({"completed", "failed"}),
    "failed": frozenset({"queued"}),  # 재시도
}

# 잡 error.code — HTTP 에러 코드가 아니라 잡 기록용이다.
TIMEOUT = "JOB_TIMEOUT"

Outcome = Literal["failed", "retried", "exhausted"]


def to_response(job: Job) -> JobResponse:
    return JobResponse.model_validate(job, from_attributes=True)


def meta(job: Job) -> dict[str, Any]:
    """폴링 힌트. 끝난 잡에는 붙이지 않는다."""
    if job.status in TERMINAL:
        return {}
    return {"retry_after_ms": settings.job_retry_after_ms}


def zombie_after(queue: str) -> timedelta:
    seconds = settings.job_zombie_seconds_ai if queue == "ai" else settings.job_zombie_seconds_io
    return timedelta(seconds=seconds)


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


def announce(session: AsyncSession, job: Job, **extra: str) -> None:
    """워커에게 알린다. 도메인 변경과 같은 트랜잭션의 outbox 로 간다(§8)."""
    emit(
        session,
        aggregate_type="job",
        aggregate_id=job.id,
        event_type="job.queued",
        payload={
            "job_id": str(job.id),
            "job_type": job.job_type,
            "queue": job.queue,
            "project_id": str(job.project_id),
            **extra,
        },
    )


async def transition(
    session: AsyncSession,
    job: Job,
    to: str,
    *,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> Job:
    """경합이 없는 전이용. 워커 경로는 claim · finish · fail 을 쓴다."""
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


async def claim(session: AsyncSession, job_id: UUID) -> Job | None:
    """`queued → running`. 이긴 워커만 잡을 받는다. 진 쪽(중복 수신)은 None."""
    stmt = (
        update(Job)
        .where(Job.id == job_id, Job.status == "queued")
        .values(
            status="running",
            attempt=Job.attempt + 1,
            started_at=func.now(),
            finished_at=None,
            error=None,
        )
        .returning(Job)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def finish(
    session: AsyncSession,
    job_id: UUID,
    attempt: int,
    to: Literal["completed", "failed"],
    *,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> bool:
    """`running → completed|failed`. 이번 실행(attempt)이 아직 주인일 때만 True."""
    values: dict[str, Any] = {"status": to, "finished_at": func.now()}
    if result is not None:
        values["result"] = result
    if error is not None:
        values["error"] = error
    stmt = (
        update(Job)
        .where(Job.id == job_id, Job.status == "running", Job.attempt == attempt)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return bool((await session.execute(stmt)).rowcount)  # type: ignore[attr-defined]


async def retry(session: AsyncSession, job_id: UUID, *, by_user: bool = False) -> Job | None:
    """`failed → queued`. 다시 워커에게 알린다.

    자동 재시도는 시도가 남았을 때만이다. 사용자가 누른 재시도(`by_user`)는 시도 수와
    무관하게 한 번 더 준다(`max_attempt` 를 늘린다) — 소진된 잡을 사람이 되살리는 길이다.
    """
    values: dict[str, Any] = {"status": "queued", "finished_at": None}
    where = [Job.id == job_id, Job.status == "failed"]
    if by_user:
        values["max_attempt"] = func.greatest(Job.max_attempt, Job.attempt + 1)
    else:
        where.append(Job.attempt < Job.max_attempt)
    stmt = (
        update(Job)
        .where(*where)
        .values(**values)
        .returning(Job)
        .execution_options(populate_existing=True)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is not None:
        announce(session, job)
    return job


async def fail(
    session: AsyncSession,
    job_id: UUID,
    attempt: int,
    error: dict[str, Any],
    *,
    retryable: bool,
) -> Outcome | None:
    """실패를 기록하고 재시도 여부를 정한다. 이번 실행이 이미 주인이 아니면 None."""
    if not await finish(session, job_id, attempt, "failed", error=error):
        return None
    if not retryable:
        return "failed"
    if await retry(session, job_id) is not None:
        log.warning("잡 %s 재시도 (시도 %d 실패: %s)", job_id, attempt, error.get("code"))
        return "retried"
    # 운영 알람. 로컬에는 CloudWatch 가 없어 ERROR 로그로 낸다.
    log.error("ALARM 잡 %s 시도 소진 (%d회) — 마지막 실패: %s", job_id, attempt, error)
    return "exhausted"


async def reap(session: AsyncSession) -> list[tuple[Job, Outcome]]:
    """좀비 회수. `running` 인데 큐별 임계치를 넘긴 잡을 `failed(JOB_TIMEOUT)` 로 내린다.

    좀비는 인프라 실패이므로 재시도 대상이다. 원래 워커가 늦게 돌아와도 `finish` 의 attempt
    대조에서 진다. 끝까지 실패한(`failed`·`exhausted`) 잡을 돌려준다 — 대상(원고 등)을
    실패로 표시하는 것은 그 잡을 만든 모듈의 몫이다(worker/sweeper.py 가 이어 준다).
    """
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(Job).where(
                Job.status == "running",
                Job.started_at.is_not(None),
            )
        )
    ).scalars()
    outcomes: list[tuple[Job, Outcome]] = []
    for job in list(rows):
        # 하트비트를 찍는 잡(ai)은 마지막 하트비트로, 아니면 선점 시각으로 판정한다(0011).
        alive_at = max(t for t in (job.started_at, job.heartbeat_at) if t is not None)
        if now - alive_at < zombie_after(job.queue):
            continue
        error = {"code": TIMEOUT, "message": f"running 상태로 {zombie_after(job.queue)} 초과"}
        outcome = await fail(session, job.id, job.attempt, error, retryable=True)
        if outcome is not None:
            outcomes.append((job, outcome))
    return outcomes


async def heartbeat(session: AsyncSession, job_id: UUID, attempt: int) -> bool:
    """이번 실행이 아직 주인일 때만 찍는다. 이미 회수됐으면 False — 워커는 그래도 끝까지 돈다
    (결과는 `finish` 의 attempt 대조에서 버려진다)."""
    stmt = (
        update(Job)
        .where(Job.id == job_id, Job.status == "running", Job.attempt == attempt)
        .values(heartbeat_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return bool((await session.execute(stmt)).rowcount)  # type: ignore[attr-defined]


async def get(session: AsyncSession, job_id: UUID) -> Job | None:
    return await repo.get_job(session, job_id)
