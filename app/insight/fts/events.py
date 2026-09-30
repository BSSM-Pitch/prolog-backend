"""도메인 이벤트 → FTS. 챕터 삭제를 받아 복선을 정리한다(명세 12항).

MSU(Ring 2)는 FTS(Ring 4)를 부를 수 없다. 챕터를 지울 때 같은 트랜잭션에 `chapter.deleted` 를
outbox 로 남기고, 이벤트 워커가 여기로 넘긴다. **자동 재지정하지 않는다:**

- 설치 챕터가 지워지면 `orphaned` — 사용자가 PATCH `setup_chapter_id` 로 다시 지정한다.
- 회수 챕터가 지워지면 회수 취소와 같다(`unresolved`). 연결 챕터는 목록에서 빠진다.

같은 이벤트가 두 번 와도 한 번만 처리한다(inbox, 부작용과 같은 트랜잭션).
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import inbox
from app.insight.fts.models import Foreshadowing, ForeshadowingChapter
from app.insight.fts.service import restatus

log = logging.getLogger(__name__)

CONSUMER = "fts"
CHAPTER_DELETED = "chapter.deleted"


async def consume(session: AsyncSession, event: dict[str, Any]) -> int:
    """정리한 복선 수. 다룰 이벤트가 아니면 inbox 도 쓰지 않는다."""
    if event.get("event_type") != CHAPTER_DELETED:
        return 0
    if not await inbox.first_time(session, CONSUMER, UUID(str(event["event_id"]))):
        return 0
    chapter_id = UUID(str(event["payload"]["chapter_id"]))
    ids = (
        await session.execute(
            select(ForeshadowingChapter.foreshadowing_id)
            .where(ForeshadowingChapter.chapter_id == chapter_id)
            .distinct()
        )
    ).scalars()
    stmt = (
        select(Foreshadowing)
        .where(Foreshadowing.id.in_(list(ids)))
        .order_by(Foreshadowing.id)  # 잠금 순서를 고정한다
        .with_for_update()
    )
    rows = list((await session.execute(stmt)).scalars())
    await session.execute(
        delete(ForeshadowingChapter).where(ForeshadowingChapter.chapter_id == chapter_id)
    )
    for row in rows:
        await restatus(session, row)
    await session.flush()
    if rows:
        log.info("챕터 %s 삭제 → 복선 %d건 정리", chapter_id, len(rows))
    return len(rows)
