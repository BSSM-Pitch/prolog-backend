"""`nl_extraction` 잡 처리 — `app.ai.jobs.run` 위에 입력과 호출만 정한다.

결과: `completed` 면 result = 네 목록 + `meta.removed_evidence_count`,
`failed` 면 패키지 에러 그대로.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.client import AIClient, Result
from app.authoring.nlcd.service import JOB_TYPE
from app.jobs.models import Job


async def _prepare(session: AsyncSession, job: Job) -> str:
    return str(job.input["source_text"])


def _call(ai: AIClient, source_text: Any) -> Result:
    return ai.run_nlcd(source_text)


async def handle(sessions: Any, job_id: UUID, ai: AIClient) -> ai_jobs.Outcome:
    return await ai_jobs.run(sessions, job_id, JOB_TYPE, ai, prepare=_prepare, call=_call)
