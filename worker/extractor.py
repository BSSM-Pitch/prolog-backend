"""추출 워커 — `python -m worker.extractor`.

io 큐에서 `job.queued` 를 받아 `manuscript_extraction` 잡을 처리한다. 잡 자체는 DB 에 있고
큐 메시지는 **알림**일 뿐이다. 같은 잡이 두 번 와도 선점(조건부 UPDATE)에서 한쪽만 이긴다.
처리에 실패한 메시지는 지우지 않고 넘어간다 — 반복 실패하면 `io-dlq` 로 간다.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.content.manuscripts.extraction import FileExtractor
from app.content.manuscripts.job_handler import handle as handle_job
from app.content.manuscripts.service import EXTRACTION_JOB_TYPE
from app.content.manuscripts.storage import S3Storage
from app.db.session import SessionFactory
from app.events.consumer import consume
from app.events.queue import IO_QUEUE, SqsQueue

log = logging.getLogger(__name__)

storage, extractor = S3Storage(), FileExtractor()


def job_id_of(body: dict[str, Any]) -> UUID | None:
    """이 워커가 다룰 메시지면 잡 id. 아니면 None(지우고 넘어간다). 깨진 id 는 예외(→ DLQ)."""
    if body.get("event_type") != "job.queued":
        return None
    payload = body.get("payload") or {}
    if payload.get("job_type") != EXTRACTION_JOB_TYPE:
        return None
    return UUID(str(payload["job_id"]))


async def handle(body: dict[str, Any]) -> None:
    job_id = job_id_of(body)
    if job_id is not None:
        log.info("잡 %s → %s", job_id, await handle_job(SessionFactory, job_id, storage, extractor))


async def main() -> None:
    log.info("extractor 시작")
    await consume(SqsQueue(), IO_QUEUE, handle)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
