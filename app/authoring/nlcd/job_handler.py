"""`nl_extraction` 잡 처리. 추출 핸들러(MSU)와 같은 두 트랜잭션 구조다.

선점을 먼저 커밋하고(스위퍼가 running 을 본다), AI 호출은 트랜잭션 밖 스레드에서 하고(패키지는
동기다), 결과는 두 번째 트랜잭션에서 잡 완료와 함께 쓴다. 완료 전이에 지면 결과를 버린다.

패키지 결과: `data` 면 `completed`(result = 네 목록 + `removed_evidence_count`), `error` 면
`failed`(error = 패키지 에러 그대로). 자동 재시도 여부는 `app.ai.client.is_retryable`.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

from app.ai.client import AIClient, is_retryable
from app.authoring.nlcd.service import JOB_TYPE
from app.jobs import service as jobs

log = logging.getLogger(__name__)

Result = Literal["skipped", "completed", "failed", "retried", "exhausted", "lost"]
Sessions = Callable[[], Any]


async def handle(sessions: Sessions, job_id: UUID, ai: AIClient) -> Result:
    async with sessions() as session:
        existing = await jobs.get(session, job_id)
        if existing is None or existing.job_type != JOB_TYPE:
            return "skipped"
        job = await jobs.claim(session, job_id)
        if job is None:
            log.info("이미 처리 중이거나 끝난 잡 %s — 중복 수신", job_id)
            return "skipped"
        await session.commit()
        attempt, source_text = job.attempt, str(job.input["source_text"])

    result = await asyncio.to_thread(ai.run_nlcd, source_text)

    async with sessions() as session:
        if "error" in result:
            error = dict(result["error"])
            outcome = await jobs.fail(
                session, job_id, attempt, error, retryable=is_retryable(error)
            )
            if outcome is None:
                await session.rollback()
                log.warning("잡 %s 실패 전이에 졌다(스위퍼가 먼저 회수)", job_id)
                return "lost"
            await session.commit()
            return outcome
        data = {
            **result["data"],
            "removed_evidence_count": (result.get("meta") or {}).get("removed_evidence_count", 0),
        }
        if not await jobs.finish(session, job_id, attempt, "completed", result=data):
            await session.rollback()
            log.warning("잡 %s 완료 전이에 졌다(스위퍼가 먼저 회수) — 결과를 버린다", job_id)
            return "lost"
        await session.commit()
    return "completed"
