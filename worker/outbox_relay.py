"""Outbox 릴레이 워커 — `python -m worker.outbox_relay`.

그냥 폴링 루프다. 한 배치가 실패해도(큐가 잠시 죽었다 등) 루프는 끝나지 않는다 — 트랜잭션이
롤백되어 `published_at` 이 찍히지 않으므로 다음 배치에서 다시 보낸다(at-least-once).
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
        try:
            async with SessionFactory() as session:
                sent = await relay_once(session, queue, settings.outbox_relay_batch)
        except Exception:
            log.exception("릴레이 배치 실패 — 다음 배치에서 다시 보낸다")
            sent = 0
        if sent:
            log.info("outbox 이벤트 %d건 발송", sent)
        else:
            # 보낼 게 없을 때(또는 실패했을 때)만 쉰다. 있으면 바로 다음 배치로 간다.
            await asyncio.sleep(settings.outbox_relay_idle_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
