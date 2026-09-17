from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.models import OutboxEvent


def emit(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    """호출한 서비스의 트랜잭션에 그대로 얹는다. commit 은 요청 단위 세션이 한다."""
    event = OutboxEvent(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    return event
