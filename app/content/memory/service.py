"""원고 기억 (Ring 2) — 1단계: 원문 조각 색인 · 검색.

스펙: prolog-ai/docs/superpowers/specs/2026-10-19-story-memory-design.md §3.1 · §4.2.

- 트리거는 이벤트가 아니라 스위퍼다. `memory_quiet_seconds` 동안 수정이 없고, 현재 본문 md5 로 만든
  조각이 없는 챕터에 `passage_index` 잡을 만든다. `idempotency_key = passage_index:챕터:md5` 라
  같은 본문에는 잡이 한 번만 생긴다 — 실패해도 본문이 바뀌기 전에는 다시 만들지 않는다.
- md5 는 SQL 의 `md5(content)` 와 파이썬 `hashlib.md5` 가 같다(UTF8). 낡음 판정이 쿼리 하나다.
- 업로드 원고처럼 챕터가 없는 원고는 색인하지 않는다 — AIQ 는 그 경우 원고 전체를 넘긴다.
"""

import hashlib
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts.models import Chapter
from app.content.memory.models import Passage
from app.jobs import service as jobs

JOB_TYPE = "passage_index"  # 0012 jobs_job_type_chk


class Hit(NamedTuple):
    passage_id: UUID
    chapter_id: UUID
    chapter_no: int
    start: int
    end: int
    text: str


def md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def idempotency_key(chapter_id: UUID, source_hash: str) -> str:
    return f"{JOB_TYPE}:{chapter_id}:{source_hash}"


# ponytail: md5(content) 를 스윕마다(_STALE)·검색 행마다(_fresh) 다시 계산한다. 챕터에
# content_md5 컬럼을 저장해 두면 없어진다 — 느려질 때.
_STALE = text(
    """
SELECT c.id, c.project_id, md5(c.content) AS source_hash
FROM content.chapters c
WHERE c.content ~ '[^[:space:]]'
  AND c.updated_at < now() - make_interval(secs => :quiet)
  AND NOT EXISTS (
      SELECT 1 FROM content.passages p
      WHERE p.chapter_id = c.id AND p.source_hash = md5(c.content))
  AND NOT EXISTS (
      SELECT 1 FROM ops.jobs j
      WHERE j.idempotency_key = 'passage_index:' || c.id || ':' || md5(c.content))
ORDER BY c.updated_at
LIMIT :limit
"""
)


async def sweep_stale(session: AsyncSession, quiet: timedelta, limit: int = 50) -> list[UUID]:
    rows = (await session.execute(_STALE, {"quiet": quiet.total_seconds(), "limit": limit})).all()
    made = []
    for chapter_id, project_id, source_hash in rows:
        job = await jobs.create(
            session,
            project_id=project_id,
            job_type=JOB_TYPE,
            target_type="chapter",
            target_id=chapter_id,
            queue="ai",
            input={"source_hash": source_hash},
            idempotency_key=idempotency_key(chapter_id, source_hash),
        )
        jobs.announce(session, job)
        made.append(chapter_id)
    return made


def _fresh(stmt: Select[Any]) -> Select[Any]:
    """현재 본문으로 만든 조각만 — 챕터 본문이 바뀌면 그 챕터의 조각은 검색에서 빠진다."""
    return stmt.join(Chapter, Chapter.id == Passage.chapter_id).where(
        Passage.source_hash == func.md5(Chapter.content)
    )


_HIT_COLUMNS = (
    Passage.id,
    Passage.chapter_id,
    Chapter.chapter_no,
    Passage.start_offset,
    Passage.end_offset,
    Passage.text,
)


async def coverage(session: AsyncSession, manuscript_id: UUID) -> tuple[int, int]:
    total = await session.scalar(
        select(func.count())
        .select_from(Chapter)
        .where(Chapter.manuscript_id == manuscript_id, Chapter.content.op("~")("[^[:space:]]"))
    )
    ready = await session.scalar(
        _fresh(select(func.count(func.distinct(Passage.chapter_id)))).where(
            Passage.manuscript_id == manuscript_id
        )
    )
    return int(ready or 0), int(total or 0)


async def search(
    session: AsyncSession, manuscript_id: UUID, vector: list[float], k: int
) -> list[Hit]:
    stmt = (
        _fresh(select(*_HIT_COLUMNS))
        .where(Passage.manuscript_id == manuscript_id)
        .order_by(Passage.embedding.cosine_distance(vector))
        .limit(k)
    )
    return [Hit(*row) for row in (await session.execute(stmt)).all()]


async def by_ids(session: AsyncSession, ids: Sequence[UUID]) -> list[Hit]:
    if not ids:
        return []
    stmt = (
        select(*_HIT_COLUMNS)
        .join(Chapter, Chapter.id == Passage.chapter_id)
        .where(Passage.id.in_(ids))
    )
    return [Hit(*row) for row in (await session.execute(stmt)).all()]
