"""큐 소비 루프 — 워커 3종이 공유한다.

**메시지 하나의 실패가 프로세스를 죽이지 않는다.** 처리 중 예외가 나면 로그를 남기고
메시지를 **지우지 않는다.** visibility timeout 이 지나면 다시 오고, `queue_max_receive_count`
번 실패하면 SQS redrive 가 `{queue}-dlq` 로 옮긴다(`SqsQueue._attach_dlq`). 그래서 poison
메시지가 큐 앞을 영원히 막지 않는다.

처리 함수는 자기 트랜잭션을 스스로 연다 — 여기서는 세션을 모른다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.events.queue import Message, Queue

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]

# 큐 자체가 응답하지 않을 때(elasticmq 재시작 등) 다시 시도하기 전 쉬는 시간.
RECEIVE_BACKOFF_SECONDS = 2.0


async def process_one(queue: Queue, queue_name: str, message: Message, handle: Handler) -> bool:
    """처리하고 지웠으면 True. 실패하면 False — 메시지는 남아 재수신·DLQ 경로로 간다."""
    try:
        await handle(message.body)
    except Exception:
        log.exception("메시지 처리 실패 (%s) — 지우지 않는다. 재수신 후 DLQ 로 간다", queue_name)
        return False
    try:
        await asyncio.to_thread(queue.delete, queue_name, message.receipt)
    except Exception:
        # 처리는 끝났다. 다시 와도 소비자가 멱등하므로 중복 부작용은 없다.
        log.exception("메시지 삭제 실패 (%s) — 재수신되면 멱등 처리로 흡수된다", queue_name)
    return True


async def consume(queue: Queue, queue_name: str, handle: Handler) -> None:
    """영원히 돈다. 받기 실패도, 처리 실패도 루프를 끝내지 않는다."""
    while True:
        try:
            messages = await asyncio.to_thread(queue.receive, queue_name, 10, 5)
        except Exception:
            log.exception("큐 수신 실패 (%s) — %.0f초 뒤 다시", queue_name, RECEIVE_BACKOFF_SECONDS)
            await asyncio.sleep(RECEIVE_BACKOFF_SECONDS)
            continue
        for message in messages:
            await process_one(queue, queue_name, message, handle)
