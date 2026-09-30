"""주기 작업 워커 — `python -m worker.sweeper`.

`sweeper_interval_seconds` 마다 두 가지를 한다. 대상이 겹치지 않는다.

1. **좀비 회수** — `running` 인데 큐별 임계치(`job_zombie_seconds_io|ai`)를 넘긴 잡을
   `failed(JOB_TIMEOUT)` 로 내리고, 시도가 남았으면 다시 `queued` 로(app.jobs.service.reap).
   끝까지 실패한 잡은 그 잡을 만든 모듈의 훅으로 대상을 실패 처리한다.
2. **업로드 콜백 유실** — 잡이 **아예 없는** 원고. 객체가 있으면 콜백 대신 추출을 건다
   (app.content.manuscripts.service.sweep_lost_uploads).

한 번의 실패가 루프를 끝내지 않는다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts import job_handler as extraction
from app.content.manuscripts import service as manuscripts
from app.content.manuscripts.storage import S3Storage, Storage
from app.core.config import settings
from app.db.session import SessionFactory
from app.insight.aiq import service as aiq
from app.insight.scds import service as scds
from app.jobs import service as jobs
from app.jobs.models import Job

log = logging.getLogger(__name__)

# 잡이 끝내 실패했을 때 대상을 어떻게 할지는 잡을 만든 모듈이 안다. job_type → 훅.
# Phase 2b 의 ai 잡들도 여기에 한 줄씩 붙는다.
ON_FAILED: dict[str, Callable[[AsyncSession, Job], Awaitable[None]]] = {
    manuscripts.EXTRACTION_JOB_TYPE: extraction.mark_failed,
    scds.JOB_TYPE: scds.mark_failed,  # 룰 후보를 조언 없는 충돌로 남긴다
    aiq.JOB_TYPE: aiq.mark_failed,  # 좀비로 끝난 답변은 failed — 화면 36 의 "다시 시도" 가 보인다
}


async def reap_zombies(session: AsyncSession) -> int:
    reaped = await jobs.reap(session)
    for job, outcome in reaped:
        if outcome in ("failed", "exhausted") and (hook := ON_FAILED.get(job.job_type)):
            await hook(session, job)
    return len(reaped)


async def sweep_once(storage: Storage) -> None:
    async with SessionFactory() as session:
        if reaped := await reap_zombies(session):
            log.warning("좀비 잡 %d건 회수", reaped)
        await session.commit()
    async with SessionFactory() as session:
        after = timedelta(seconds=settings.upload_sweep_after_seconds)
        if started := await manuscripts.sweep_lost_uploads(session, storage, after):
            log.warning("콜백 유실 원고 %d건 추출 시작: %s", len(started), started)
        await session.commit()


async def main() -> None:
    storage = S3Storage()
    log.info("sweeper 시작 (주기 %.0f초)", settings.sweeper_interval_seconds)
    while True:
        try:
            await sweep_once(storage)
        except Exception:
            log.exception("sweep 실패 — 다음 주기에 다시")
        await asyncio.sleep(settings.sweeper_interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
