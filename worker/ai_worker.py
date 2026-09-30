"""AI 워커 — `python -m worker.ai_worker`.

ai 큐에서 `job.queued` 를 받아 잡 종류별 핸들러(`HANDLERS`)에 넘긴다. 잡은 DB 에 있고
메시지는 알림일 뿐이다 — 같은 잡이 두 번 와도 선점에서 한쪽만 이긴다. 한 큐에 소비자를 둘
붙이지 않으므로(SQS) ai 잡은 전부 이 워커가 받는다.

AI 호출은 오래 걸린다(호출당 30~90초 · 최대 3회). ai 큐의 visibility timeout 은
`queue_visibility_seconds_ai`(15분)이다. 실제 LLM 을 부르려면 `OPENROUTER_API_KEY` 가 필요하고,
`USE_FAKE_LLM=1` 이면 패키지가 빈 응답을 만든다(로컬 전 구간 확인용).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.ai.client import AIClient, PrologAI
from app.authoring.nlcd import job_handler as nlcd
from app.authoring.nlcd.service import JOB_TYPE as NLCD_JOB
from app.authoring.rex import extraction as rex
from app.db.session import SessionFactory
from app.events.consumer import consume
from app.events.queue import AI_QUEUE, SqsQueue
from app.insight.aiq import service as aiq
from app.insight.scds import service as scds

log = logging.getLogger(__name__)

Handler = Callable[[Any, UUID, AIClient], Awaitable[str]]
HANDLERS: dict[str, Handler] = {
    NLCD_JOB: nlcd.handle,
    rex.JOB_TYPE: rex.handle,
    aiq.JOB_TYPE: aiq.handle,
    scds.JOB_TYPE: scds.handle,
}

ai: AIClient = PrologAI()


def route(body: dict[str, Any]) -> tuple[Handler, UUID] | None:
    """이 워커가 다룰 메시지면 (핸들러, 잡 id). 아니면 None(지우고 넘어간다). 깨진 id 는 예외."""
    if body.get("event_type") != "job.queued":
        return None
    payload = body.get("payload") or {}
    handler = HANDLERS.get(str(payload.get("job_type")))
    if handler is None:
        log.warning("다룰 수 없는 ai 잡 종류 %s — 건너뛴다", payload.get("job_type"))
        return None
    return handler, UUID(str(payload["job_id"]))


async def handle(body: dict[str, Any]) -> None:
    routed = route(body)
    if routed is not None:
        handler, job_id = routed
        log.info("잡 %s → %s", job_id, await handler(SessionFactory, job_id, ai))


async def main() -> None:
    log.info("ai 워커 시작")
    await consume(SqsQueue(), AI_QUEUE, handle)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
