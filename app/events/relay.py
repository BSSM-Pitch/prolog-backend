"""Outbox 릴레이.

`published_at IS NULL` 을 집어 큐로 보내고 표시한다. 도메인 트랜잭션은 outbox 행만
남기고 끝나므로(§8), 실제 발송은 여기서 일어난다.

**at-least-once 다.** 발송 후 표시 사이에 죽으면 같은 이벤트가 다시 간다. 수신 측이
멱등해야 한다 — 중복 제거는 Phase 2(멱등키)의 몫이지 릴레이의 몫이 아니다.

`FOR UPDATE SKIP LOCKED` 로 릴레이를 여러 개 띄워도 같은 행을 두 번 보내지 않는다.
"""

import asyncio
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.models import OutboxEvent
from app.events.queue import IO_QUEUE, Queue


def message(event: OutboxEvent) -> dict[str, Any]:
    """수신자가 원본 테이블을 다시 읽지 않아도 되게 자기완결적으로 싣는다."""
    return {
        "event_id": str(event.id),
        "aggregate_type": event.aggregate_type,
        "aggregate_id": str(event.aggregate_id),
        "event_type": event.event_type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


async def relay_once(session: AsyncSession, queue: Queue, batch: int = 100) -> int:
    """한 배치를 보내고 보낸 개수를 돌려준다. 커밋까지 한다."""
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.created_at)
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    events = list((await session.execute(stmt)).scalars())
    for event in events:
        await asyncio.to_thread(queue.send, IO_QUEUE, message(event))
        event.published_at = func.now()
    await session.commit()
    return len(events)
