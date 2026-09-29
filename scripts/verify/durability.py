"""워커 내구성 판정 — 실물(elasticmq · PG) 위에서 알려진 두 결함이 **재현되지 않는지** 본다.

Phase 2a 이전에는 둘 다 재현됐다. 지금은 재현되면 exit 1 이다.
전제: 워커 4종(outbox_relay · notifier · extractor · sweeper)이 떠 있다.

uv run python -m scripts.verify.durability poison <email>
    notify 큐에 invitation_id 가 UUID 가 아닌 초대 이벤트를 넣는다. <email> 은 가입된
    사용자여야 한다(미가입이면 notifier 가 UUID 파싱 전에 None 으로 빠진다).
    통과: notifier 프로세스가 살아 있고, 메시지가 `notify-dlq` 로 옮겨졌다.
    (visibility timeout 30초 × maxReceiveCount 번 받아져야 옮겨지므로 2분쯤 걸린다.)

uv run python -m scripts.verify.durability dup <outbox_event_id>
    이미 발송된 outbox 행의 메시지를 릴레이와 **같은 함수(relay.message)** 로 만들어
    notify 큐에 한 번 더 보낸다. 릴레이가 발송 후 표시 전에 죽어 재발송한 상황과 같다.
    통과: 그 이벤트가 만든 알림이 늘지 않았다.

uv run python -m scripts.verify.durability drain
    poison 판정이 DLQ 에 남긴 메시지를 치운다. marker 가 붙은 것만 지운다.
"""

import asyncio
import json
import subprocess
import sys
import time
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SessionFactory
from app.events.models import OutboxEvent
from app.events.queue import DLQ_SUFFIX, NOTIFY_QUEUE, SqsQueue
from app.events.relay import message
from app.platform_.notifications.models import Notification

# poison 의 event_id 앞 8자리. UUID 로 유효해야 inbox 를 지나 invitation_id 파싱에서 실패한다.
MARKER = "deadbeef"
DLQ = NOTIFY_QUEUE + DLQ_SUFFIX
VISIBILITY_SECONDS = 30  # elasticmq 기본값. 큐에 따로 걸지 않았다.


def verdict(ok: bool, message_: str) -> None:
    print(("통과 — " if ok else "재현됨 — ") + message_)
    sys.exit(0 if ok else 1)


def notifier_pids() -> set[str]:
    out = subprocess.run(["pgrep", "-f", "worker.notifier"], capture_output=True, text=True)
    return set(out.stdout.split())


def dlq_markers(queue: SqsQueue) -> list[Any]:
    """DLQ 에서 marker 메시지를 본다(받았다가 바로 다시 보이게 돌려놓는다)."""
    client, url = queue._client, queue._url(DLQ)  # 판정 스크립트라 내부 클라이언트를 쓴다
    found = []
    for m in client.receive_message(
        QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=1, VisibilityTimeout=0
    ).get("Messages", []):
        if str(json.loads(m["Body"]).get("event_id", "")).startswith(MARKER):
            found.append(m)
    return found


def poison(email: str) -> None:
    queue = SqsQueue()
    before = notifier_pids()
    if not before:
        sys.exit("notifier 가 떠 있지 않다")
    event_id = MARKER + str(uuid4())[8:]
    queue.send(
        NOTIFY_QUEUE,
        {
            "event_id": event_id,
            "event_type": "team.invited",
            "payload": {"invited_email": email, "invitation_id": "not-a-uuid"},
        },
    )
    budget = VISIBILITY_SECONDS * settings.queue_max_receive_count + 60
    print(f"poison 발송 ({event_id}). DLQ 로 옮겨지기를 최대 {budget}초 기다린다")
    deadline = time.time() + budget
    while time.time() < deadline:
        if not notifier_pids() & before:
            verdict(False, "notifier 프로세스가 죽었다")
        if dlq_markers(queue):
            verdict(True, f"notifier 생존 · poison 은 {DLQ} 로 옮겨졌다")
        time.sleep(5)
    verdict(False, f"{budget}초 안에 {DLQ} 로 옮겨지지 않았다 — 큐 앞을 막고 있다")


async def _notifications_for(event: OutboxEvent) -> int:
    async with SessionFactory() as session:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.resource_id == UUID(str(event.payload["invitation_id"])))
        )
        return int((await session.execute(stmt)).scalar_one())


async def dup(event_id: str) -> None:
    async with SessionFactory() as session:
        event = await session.get(OutboxEvent, UUID(event_id))
    if event is None:
        sys.exit(f"outbox {event_id} 없음")
    before = await _notifications_for(event)
    SqsQueue().send(NOTIFY_QUEUE, message(event))
    print(f"재발송 {event.event_type} {event_id} — 알림 {before}건에서 시작")
    await asyncio.sleep(10)
    after = await _notifications_for(event)
    verdict(after == before, f"같은 이벤트 재수신 후 알림 {before} → {after}건")


def drain() -> None:
    queue = SqsQueue()
    removed = 0
    for m in dlq_markers(queue):
        queue.delete(DLQ, m["ReceiptHandle"])
        removed += 1
    print(f"{DLQ} 에서 poison {removed}건 삭제")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "poison":
        poison(args[0])
    elif cmd == "dup":
        asyncio.run(dup(args[0]))
    elif cmd == "drain":
        drain()
