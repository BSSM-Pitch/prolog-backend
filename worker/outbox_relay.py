"""Outbox 릴레이 워커 — `python -m worker.outbox_relay`.

Celery 가 아니라 그냥 폴링 루프다. Phase 2 에서 잡 디스패처가 붙을 때 다시 본다.
"""

import asyncio
import logging

from app.core.config import settings
from app.db.session import SessionFactory
from app.events.queue import SqsQueue
from app.events.relay import relay_once

log = logging.getLogger(__name__)


async def main() -> None:
    queue = SqsQueue()
    log.info("outbox relay 시작 (endpoint=%s)", settings.sqs_endpoint_url)
    while True:
        async with SessionFactory() as session:
            sent = await relay_once(session, queue, settings.outbox_relay_batch)
        if sent:
            log.info("outbox 이벤트 %d건 발송", sent)
        else:
            # 보낼 게 없을 때만 쉰다. 있으면 바로 다음 배치로 간다.
            await asyncio.sleep(settings.outbox_relay_idle_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
