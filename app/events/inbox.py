"""이벤트 소비 멱등 (inbox).

릴레이는 at-least-once 다. 소비자는 부작용을 내기 **전에**, 같은 트랜잭션 안에서 이 함수로
이벤트를 선점한다. 이미 처리한 이벤트면 False — 아무것도 하지 말고 메시지를 지운다.
부작용이 실패해 롤백되면 선점도 함께 사라지므로 재수신 때 다시 처리된다.
"""

from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.models import ProcessedEvent


async def first_time(session: AsyncSession, consumer: str, event_id: UUID) -> bool:
    stmt = (
        insert(ProcessedEvent)
        .values(consumer=consumer, event_id=event_id)
        .on_conflict_do_nothing(index_elements=["consumer", "event_id"])
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)  # type: ignore[attr-defined]
