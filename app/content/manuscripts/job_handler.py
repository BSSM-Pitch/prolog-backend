"""manuscript_extraction 잡 처리 (ROADMAP 의 모듈당 job_handler.py).

**트랜잭션이 둘이다.** 선점(`queued → running`)을 먼저 커밋해야 스위퍼가 `running` 을 보고
좀비를 회수할 수 있다. 추출(S3 읽기 · 파싱)은 트랜잭션 밖에서 하고, 결과는 두 번째 트랜잭션에서
잡 완료와 **함께** 원고에 쓴다. 완료 전이가 지면(스위퍼가 먼저 내렸다) 원고도 쓰지 않는다.

실패는 두 종류다.
- 인프라(스토리지 읽기): 재시도한다. 시도를 다 쓰면 원고를 `failed` 로.
- 도메인(형식 미지원 · 파싱 실패 · 원고 없음): 다시 해도 같다. 즉시 원고를 `failed` 로.
사유는 **잡의 `error`** 에 남는다. 원고에 error 컬럼을 만들지 않는다.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts import repository as repo
from app.content.manuscripts.extraction import Extractor, UnsupportedFormat
from app.content.manuscripts.service import EXTRACTION_JOB_TYPE
from app.content.manuscripts.storage import Storage
from app.jobs import service as jobs
from app.jobs.models import Job

log = logging.getLogger(__name__)

FAILED = "TEXT_EXTRACTION_FAILED"
UNSUPPORTED = "UNSUPPORTED_FILE_FORMAT"
# ASSUMPTION: 잡 기록용 내부 코드다(HTTP 로 나가지 않는다). 재시도 대상인 인프라 실패를 구분한다.
STORAGE_FAILED = "STORAGE_READ_FAILED"

Result = Literal["skipped", "completed", "failed", "retried", "exhausted", "lost"]
Sessions = Callable[[], Any]  # async_sessionmaker — 호출하면 AsyncSession 컨텍스트


async def mark_failed(session: AsyncSession, job: Job) -> None:
    """잡이 끝내 실패했을 때 원고를 `failed` 로. 스위퍼(좀비 소진)도 이걸 부른다."""
    if job.target_id is None:
        return
    manuscript = await repo.get_manuscript(session, job.project_id, job.target_id)
    if manuscript is not None:
        manuscript.status = "failed"


async def handle(
    sessions: Sessions, job_id: UUID, storage: Storage, extractor: Extractor
) -> Result:
    async with sessions() as session:
        existing = await jobs.get(session, job_id)
        if existing is None or existing.job_type != EXTRACTION_JOB_TYPE:
            return "skipped"
        job = await jobs.claim(session, job_id)
        if job is None:
            log.info("이미 처리 중이거나 끝난 잡 %s — 중복 수신", job_id)
            return "skipped"
        await session.commit()
        attempt, project_id, target_id = job.attempt, job.project_id, job.target_id
        key, file_format = str(job.input["file_key"]), str(job.input["file_format"])

    try:
        data = await asyncio.to_thread(storage.read, key)
    except Exception as exc:
        error = {"code": STORAGE_FAILED, "message": str(exc)}
        return await _fail(sessions, job_id, attempt, error, retryable=True)

    try:
        text = await asyncio.to_thread(extractor.extract, file_format, data)
    except UnsupportedFormat:
        error = {"code": UNSUPPORTED, "format": file_format}
        return await _fail(sessions, job_id, attempt, error, retryable=False)
    except Exception as exc:  # 파서가 던지는 것은 파일 탓이다 — 다시 해도 같다
        return await _fail(
            sessions, job_id, attempt, {"code": FAILED, "message": str(exc)}, retryable=False
        )

    async with sessions() as session:
        manuscript = await repo.get_manuscript(session, project_id, UUID(str(target_id)))
        if manuscript is None:
            await session.rollback()
            error = {"code": FAILED, "message": "원고가 없습니다"}
            return await _fail(sessions, job_id, attempt, error, retryable=False)
        if not await jobs.finish(
            session, job_id, attempt, "completed", result={"chars": len(text)}
        ):
            await session.rollback()
            log.warning("잡 %s 완료 전이에 졌다(스위퍼가 먼저 회수) — 결과를 버린다", job_id)
            return "lost"
        manuscript.content = text
        manuscript.status = "ready"
        await session.commit()
    return "completed"


async def _fail(
    sessions: Sessions, job_id: UUID, attempt: int, error: dict[str, Any], *, retryable: bool
) -> Result:
    async with sessions() as session:
        outcome = await jobs.fail(session, job_id, attempt, error, retryable=retryable)
        if outcome is None:
            await session.rollback()
            log.warning("잡 %s 실패 전이 실패 — 스위퍼가 먼저 회수했다", job_id)
            return "lost"
        if outcome in ("failed", "exhausted"):
            job = await jobs.get(session, job_id)
            if job is not None:
                await mark_failed(session, job)
        await session.commit()
    return outcome
