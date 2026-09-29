"""워커 내구성 재현 — 고치지 않는다, 알려진 문제가 실제로 그런지만 본다.

uv run python -m scripts.verify.durability poison <email>
    notify 큐에 invitation_id 가 UUID 가 아닌 초대 이벤트를 넣는다. <email> 은 가입된
    사용자여야 한다(미가입이면 notifier 가 UUID 파싱 전에 None 으로 빠진다).
    → notifier 가 ValueError 로 죽는다. 메시지는 지워지지 않아 재기동하면 또 죽는다.

uv run python -m scripts.verify.durability dup <outbox_event_id>
    이미 발송된 outbox 행의 메시지를 릴레이와 **같은 함수(relay.message)** 로 만들어
    notify 큐에 한 번 더 보낸다. 릴레이가 발송 후 표시 전에 죽어 재발송한 상황과 같다.
    → 알림이 하나 더 생긴다.

uv run python -m scripts.verify.durability drain
    poison 메시지를 큐에서 치운다(재현 후 정리용). marker 가 붙은 것만 지운다.
"""

import asyncio
import sys
from uuid import UUID

from app.db.session import SessionFactory
from app.events.models import OutboxEvent
from app.events.queue import NOTIFY_QUEUE, SqsQueue
from app.events.relay import message

MARKER = "verify-durability-poison"


def poison(email: str) -> None:
    SqsQueue().send(
        NOTIFY_QUEUE,
        {
            "event_id": MARKER,
            "event_type": "team.invited",
            "payload": {"invited_email": email, "invitation_id": "not-a-uuid"},
        },
    )
    print("poison 발송")


async def dup(event_id: str) -> None:
    async with SessionFactory() as session:
        event = await session.get(OutboxEvent, UUID(event_id))
        if event is None:
            sys.exit(f"outbox {event_id} 없음")
        SqsQueue().send(NOTIFY_QUEUE, message(event))
    print(f"재발송 {event.event_type} {event_id}")


def drain() -> None:
    queue, removed = SqsQueue(), 0
    for _ in range(10):
        for m in queue.receive(NOTIFY_QUEUE, 10, 2):
            if m.body.get("event_id") == MARKER:
                queue.delete(NOTIFY_QUEUE, m.receipt)
                removed += 1
    print(f"poison {removed}건 삭제 (나머지 메시지는 visibility timeout 뒤 다시 보인다)")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "poison":
        poison(args[0])
    elif cmd == "dup":
        asyncio.run(dup(args[0]))
    elif cmd == "drain":
        drain()
