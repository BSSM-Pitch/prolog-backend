"""manuscript_extraction 잡 처리 (ROADMAP 의 모듈당 job_handler.py).

큐는 **at-least-once** 다. 같은 잡이 두 번 와도 `queued` 가 아니면 건너뛴다 —
조건 없는 중복 실행 방지는 Phase 2 의 조건부 UPDATE 가 맡고, 여기서는 상태만 본다.

실패는 **잡의 `error`** 에 남기고 원고는 `failed` 로 둔다. 원고에 error 컬럼을 만들지 않는다.
"""

import asyncio
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts import repository as repo
from app.content.manuscripts.extraction import Extractor, UnsupportedFormat
from app.content.manuscripts.service import EXTRACTION_JOB_TYPE
from app.content.manuscripts.storage import Storage
from app.jobs import service as jobs

log = logging.getLogger(__name__)

FAILED = "TEXT_EXTRACTION_FAILED"
UNSUPPORTED = "UNSUPPORTED_FILE_FORMAT"


async def handle(
    session: AsyncSession, job_id: UUID, storage: Storage, extractor: Extractor
) -> bool:
    """처리했으면 True. 대상이 아니거나 이미 처리된 잡이면 False."""
    job = await jobs.get(session, job_id)
    if job is None or job.job_type != EXTRACTION_JOB_TYPE:
        return False
    if job.status != "queued":
        log.info("이미 처리된 잡 %s (%s)", job_id, job.status)
        return False

    await jobs.transition(session, job, "running")
    manuscript = await repo.get_manuscript(session, job.project_id, UUID(str(job.target_id)))
    if manuscript is None:
        await jobs.transition(
            session, job, "failed", error={"code": FAILED, "message": "원고가 없습니다"}
        )
        return True

    key = str(job.input["file_key"])
    file_format = str(job.input["file_format"])
    try:
        data = await asyncio.to_thread(storage.read, key)
        text = await asyncio.to_thread(extractor.extract, file_format, data)
    except UnsupportedFormat:
        manuscript.status = "failed"
        await jobs.transition(
            session, job, "failed", error={"code": UNSUPPORTED, "format": file_format}
        )
        return True
    except Exception as exc:  # 추출기·스토리지의 어떤 실패든 잡에 남는다
        manuscript.status = "failed"
        await jobs.transition(session, job, "failed", error={"code": FAILED, "message": str(exc)})
        return True

    manuscript.content = text
    manuscript.status = "ready"
    await jobs.transition(session, job, "completed", result={"chars": len(text)})
    return True
