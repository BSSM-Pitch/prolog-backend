"""알림 워커 — `python -m worker.notifier`.

릴레이가 io 큐로 보낸 Outbox 이벤트를 받아 알림을 만든다. 릴레이는 at-least-once 이므로
같은 이벤트가 두 번 올 수 있다 — **중복 알림 억제는 Phase 2(멱등키)의 몫이다.**
"""

import asyncio
import logging

from app.api.notify import notify_from_event
from app.db.session import SessionFactory
from app.events.queue import IO_QUEUE, SqsQueue

log = logging.getLogger(__name__)


async def main() -> None:
    queue = SqsQueue()
    log.info("notifier 시작")
    while True:
        messages = await asyncio.to_thread(queue.receive, IO_QUEUE, 10, 5)
        for message in messages:
            async with SessionFactory() as session:
                created = await notify_from_event(session, message.body)
                await session.commit()
            if created is not None:
                log.info("알림 생성 %s", created.notification_id)
            await asyncio.to_thread(queue.delete, IO_QUEUE, message.receipt)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
