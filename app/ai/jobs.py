"""AI 잡 한 번 실행하기 — 모든 AI 모듈 핸들러가 공유하는 뼈대.

MSU 추출 핸들러와 같은 **두 트랜잭션**이다.

1. 선점(`queued → running`)하고 `prepare` 로 AI 입력을 만든 뒤 **커밋**한다 — 스위퍼가 running 을
   봐야 좀비를 줍는다. `prepare` 가 도메인 에러를 던지면(원고가 사라짐 등) 재시도 없이
   실패로 끝낸다.
2. 트랜잭션 **밖** 스레드에서 `call` 로 패키지를 부른다(동기 · 수십 초).
3. 두 번째 트랜잭션에서 잡 전이와 **함께** `on_success`/`on_failure` 를 부른다 — 결과를 대상
   (AIQ 메시지 · 충돌 · 구조 지도)에 쓰는 일이다. 전이에 지면(스위퍼가 먼저 회수) 결과를 버린다.

잡의 `result` 는 패키지 `data` 에 `meta` 를 얹은 것이다. 실패는 패키지 `error` 그대로 `error` 에.
자동 재시도 여부는 `client.is_retryable`.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, Result, is_retryable
from app.core.errors import AppError
from app.jobs import service as jobs
from app.jobs.models import Job

log = logging.getLogger(__name__)

Outcome = Literal["skipped", "completed", "failed", "retried", "exhausted", "lost"]
Sessions = Callable[[], Any]  # async_sessionmaker
Prepare = Callable[[AsyncSession, Job], Awaitable[Any]]
Call = Callable[[AIClient, Any], Result]
OnSuccess = Callable[[AsyncSession, Job, dict[str, Any]], Awaitable[None]]
OnFailure = Callable[[AsyncSession, Job, dict[str, Any]], Awaitable[None]]


async def run(
    sessions: Sessions,
    job_id: UUID,
    job_type: str,
    ai: AIClient,
    *,
    prepare: Prepare,
    call: Call,
    on_success: OnSuccess | None = None,
    on_failure: OnFailure | None = None,
) -> Outcome:
    async with sessions() as session:
        existing = await jobs.get(session, job_id)
        if existing is None or existing.job_type != job_type:
            return "skipped"
        job = await jobs.claim(session, job_id)
        if job is None:
            log.info("이미 처리 중이거나 끝난 잡 %s — 중복 수신", job_id)
            return "skipped"
        try:
            args = await prepare(session, job)
        except AppError as exc:
            error = {"code": exc.code, "message": exc.message, "details": exc.details}
            outcome = await jobs.fail(session, job_id, job.attempt, error, retryable=False)
            if on_failure is not None:
                await on_failure(session, job, error)
            await session.commit()
            return outcome or "lost"
        await session.commit()
        attempt = job.attempt

    result = await asyncio.to_thread(call, ai, args)

    async with sessions() as session:
        job = await jobs.get(session, job_id)
        assert job is not None
        if "error" in result:
            error = dict(result["error"])
            outcome = await jobs.fail(
                session, job_id, attempt, error, retryable=is_retryable(error)
            )
            if outcome is None:
                await session.rollback()
                log.warning("잡 %s 실패 전이에 졌다(스위퍼가 먼저 회수)", job_id)
                return "lost"
            # 재시도로 다시 queued 가 됐으면 대상은 아직 기다리는 중이다
            if outcome in ("failed", "exhausted") and on_failure is not None:
                await on_failure(session, job, error)
            await session.commit()
            return outcome
        data = {**result["data"], "meta": result.get("meta") or {}}
        if not await jobs.finish(session, job_id, attempt, "completed", result=data):
            await session.rollback()
            log.warning("잡 %s 완료 전이에 졌다(스위퍼가 먼저 회수) — 결과를 버린다", job_id)
            return "lost"
        if on_success is not None:
            await on_success(session, job, data)
        await session.commit()
    return "completed"
