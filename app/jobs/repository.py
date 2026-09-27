from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import Job


async def get_job(session: AsyncSession, job_id: UUID) -> Job | None:
    return await session.get(Job, job_id)


async def add_job(session: AsyncSession, job: Job) -> Job:
    session.add(job)
    await session.flush()
    return job
