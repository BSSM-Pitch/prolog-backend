"""추출 워커 — `python -m worker.extractor`.

io 큐에서 `job.queued` 를 받아 `manuscript_extraction` 잡을 처리한다. 잡 자체는 DB 에 있고
큐 메시지는 **알림**일 뿐이라, 메시지를 잃어도 잡은 남는다(회수는 Phase 2).

한 번에 한 잡씩 처리한다. 동시 실행은 Phase 2 에서 조건부 UPDATE 와 함께 본다.
"""

import asyncio
import logging
from uuid import UUID

from app.content.manuscripts.extraction import FileExtractor
from app.content.manuscripts.job_handler import handle
from app.content.manuscripts.service import EXTRACTION_JOB_TYPE
from app.content.manuscripts.storage import S3Storage
from app.db.session import SessionFactory
from app.events.queue import IO_QUEUE, SqsQueue

log = logging.getLogger(__name__)


def _job_id(body: dict) -> UUID | None:
    if body.get("event_type") != "job.queued":
        return None
    payload = body.get("payload") or {}
    if payload.get("job_type") != EXTRACTION_JOB_TYPE:
        return None
    return UUID(str(payload["job_id"]))


async def main() -> None:
    queue, storage, extractor = SqsQueue(), S3Storage(), FileExtractor()
    log.info("extractor 시작")
    while True:
        messages = await asyncio.to_thread(queue.receive, IO_QUEUE, 10, 5)
        for message in messages:
            job_id = _job_id(message.body)
            if job_id is not None:
                async with SessionFactory() as session:
                    handled = await handle(session, job_id, storage, extractor)
                    await session.commit()
                log.info("잡 %s 처리=%s", job_id, handled)
            await asyncio.to_thread(queue.delete, IO_QUEUE, message.receipt)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
