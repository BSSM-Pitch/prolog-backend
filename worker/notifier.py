"""알림 워커 — `python -m worker.notifier`.

릴레이가 notify 큐로 보낸 Outbox 이벤트를 받아 알림을 만든다. 같은 이벤트가 두 번 와도
알림은 하나다(`app.api.notify.consume` 의 inbox). 처리에 실패한 메시지는 지우지 않고
넘어간다 — 반복 실패하면 `notify-dlq` 로 간다(`app.events.consumer`).
"""

import asyncio
import logging
from typing import Any

from app.api.notify import consume as consume_event
from app.db.session import SessionFactory
from app.events.consumer import consume
from app.events.queue import NOTIFY_QUEUE, SqsQueue

log = logging.getLogger(__name__)


async def handle(body: dict[str, Any]) -> None:
    async with SessionFactory() as session:
        created = await consume_event(session, body)
        await session.commit()
    if created is not None:
        log.info("알림 생성 %s", created.notification_id)


async def main() -> None:
    log.info("notifier 시작")
    await consume(SqsQueue(), NOTIFY_QUEUE, handle)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
