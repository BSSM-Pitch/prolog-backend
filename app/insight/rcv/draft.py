"""Durable, cached drafts for the writer's relationship form."""

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.client import AIClient
from app.ai.relationship_draft import validate
from app.authoring.ass.models import Character
from app.content.manuscripts.models import Chapter, Manuscript
from app.core import errors
from app.jobs import service as jobs
from app.jobs.models import Job

JOB_TYPE = "relationship_draft"
MAX_CHARS = 12000


async def get(session: AsyncSession, project_id: UUID, draft_id: UUID) -> Job:
    job = await session.scalar(
        select(Job).where(
            Job.id == draft_id, Job.project_id == project_id, Job.job_type == JOB_TYPE
        )
    )
    if job is None:
        raise errors.InvalidInput("관계 초안을 찾을 수 없어요.")
    return job


async def view(session: AsyncSession, job: Job) -> dict[str, Any]:
    chapter = await session.get(Chapter, UUID(job.input["chapter_id"]))
    current_hash = hashlib.sha256(chapter.content.encode()).hexdigest() if chapter else None
    return {
        "draft_id": str(job.id),
        "status": job.status,
        "chapter_id": job.input["chapter_id"],
        "source_hash": job.input["source_hash"],
        "stale": current_hash != job.input["chapter_hash"],
        "analyzed_chars": len(job.input["payload"]["text"]),
        "total_chars": job.input["total_chars"],
        "result": job.result if job.status == "completed" else None,
        "error": (job.error or {}).get("message") if job.status == "failed" else None,
    }


async def request(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    chapter_id: UUID,
    user_id: UUID,
    selected_pair: list[UUID] | None = None,
    retry: bool = False,
) -> Job:
    manuscript = await session.scalar(
        select(Manuscript)
        .where(Manuscript.id == manuscript_id, Manuscript.project_id == project_id)
        .with_for_update()
    )
    if manuscript is None:
        raise errors.ManuscriptNotFound()
    chapter = await session.scalar(
        select(Chapter).where(
            Chapter.id == chapter_id,
            Chapter.manuscript_id == manuscript_id,
            Chapter.project_id == project_id,
        )
    )
    if chapter is None:
        raise errors.ChapterNotFound()
    if not chapter.content.strip():
        raise errors.InvalidInput("내용이 있는 장을 골라 주세요.")
    if selected_pair and (len(selected_pair) != 2 or len(set(selected_pair)) != 2):
        raise errors.InvalidInput("서로 다른 두 인물을 골라 주세요.")
    query = select(Character).where(Character.project_id == project_id).order_by(Character.id)
    if selected_pair:
        query = query.where(Character.id.in_(selected_pair))
    characters = list((await session.scalars(query.limit(201))).all())
    if selected_pair and len(characters) != 2:
        raise errors.CharacterNotFound()
    if not 2 <= len(characters) <= 200:
        raise errors.InvalidInput(
            "등록된 인물이 두 명 이상 필요해요. 인물이 200명을 넘으면 두 인물을 먼저 골라 주세요."
        )
    payload = {
        "text": chapter.content[:MAX_CHARS],
        "chapter_title": chapter.title,
        "characters": [{"id": str(person.id), "name": person.name} for person in characters],
        "selected_pair": sorted(str(value) for value in selected_pair) if selected_pair else None,
    }
    chapter_hash = hashlib.sha256(chapter.content.encode()).hexdigest()
    source_hash = hashlib.sha256(
        json.dumps(
            ["relationship-draft-v1", str(chapter_id), chapter_hash, payload],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    existing = await session.scalar(
        select(Job)
        .where(
            Job.project_id == project_id,
            Job.job_type == JOB_TYPE,
            Job.input["source_hash"].astext == source_hash,
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if existing:
        if existing.status == "failed" and retry:
            await jobs.retry(session, existing.id, by_user=True)
            await session.refresh(existing)
        return existing
    job = await jobs.create(
        session,
        project_id=project_id,
        job_type=JOB_TYPE,
        target_type="chapter",
        target_id=chapter_id,
        queue="ai",
        created_by=user_id,
        input={
            "manuscript_id": str(manuscript_id),
            "chapter_id": str(chapter_id),
            "source_hash": source_hash,
            "chapter_hash": chapter_hash,
            "total_chars": len(chapter.content),
            "payload": payload,
        },
    )
    job.max_attempt = 1
    jobs.announce(session, job)
    await session.flush()
    await session.refresh(job)
    return job


async def prepare(session: AsyncSession, job: Job) -> dict[str, Any]:
    return job.input["payload"]


def call(ai: AIClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = ai.run_relationship_draft(payload)
    if "error" in response:
        return response
    try:
        return {"data": validate(response["data"], payload)}
    except (ValueError, KeyError, TypeError):
        return {
            "error": {
                "code": "RELATIONSHIP_DRAFT_INVALID",
                "message": "초안의 인물이나 원문 근거를 확인하지 못했어요.",
            }
        }


async def handle(sessions: ai_jobs.Sessions, job_id: UUID, ai: AIClient) -> str:
    return await ai_jobs.run(sessions, job_id, JOB_TYPE, ai, prepare=prepare, call=call)
