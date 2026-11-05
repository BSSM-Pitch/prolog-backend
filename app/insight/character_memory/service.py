"""원고 스냅샷을 고정하고, 작은 호출 묶음마다 체크포인트를 영속화한다."""

import asyncio
from contextlib import suppress
from hashlib import sha256
from typing import Any
from uuid import UUID

from prolog_ai.memory.schema import Character as CharacterRef
from prolog_ai.memory.schema import Scene
from prolog_ai.memory.source import digest, snapshot_id, validate_memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.client import AIClient, is_retryable
from app.authoring.ass.models import Character
from app.content.manuscripts.models import Chapter, Manuscript
from app.core import errors
from app.insight.character_memory import discovery
from app.insight.character_memory.projection import character_counts, dossier
from app.jobs import service as jobs
from app.jobs.models import Job

JOB_TYPE = "character_memory"


async def source(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, *, lock: bool = False
) -> dict[str, Any]:
    stmt = select(Manuscript).where(
        Manuscript.id == manuscript_id, Manuscript.project_id == project_id
    )
    if lock:
        stmt = stmt.with_for_update()
    manuscript = (await session.execute(stmt)).scalar_one_or_none()
    if manuscript is None:
        raise errors.ManuscriptNotFound()
    chapters = list(
        (
            await session.scalars(
                select(Chapter)
                .where(Chapter.manuscript_id == manuscript_id, Chapter.project_id == project_id)
                .order_by(Chapter.chapter_no)
            )
        ).all()
    )
    people = list(
        (
            await session.scalars(
                select(Character)
                .where(Character.project_id == project_id, Character.status == "active")
                .order_by(Character.id)
            )
        ).all()
    )
    scenes, titles = [], {}
    rows: list[tuple[str, int, str | None, str, str | None]] = [
        (str(c.id), c.chapter_no, c.title, c.content, str(c.id)) for c in chapters
    ]
    if not chapters:
        rows = [(str(manuscript.id), 1, manuscript.title, manuscript.content or "", None)]
    for sid, order, title, text, chapter_id in rows:
        if not text.strip():
            continue
        scenes.append(
            {
                "scene_id": sid,
                "chapter_id": chapter_id,
                "order": order,
                "revision": sha256(text.encode()).hexdigest(),
                "text": text,
            }
        )
        titles[sid] = (
            f"{order}장 · {title}"
            if chapter_id and title
            else (f"{order}장" if chapter_id else manuscript.title)
        )
    characters = [
        {"character_id": str(c.id), "name": c.name, **({"aliases": c.aliases} if c.aliases else {})}
        for c in people
    ]
    return {
        "project_id": str(project_id),
        "manuscript_id": str(manuscript_id),
        "scenes": scenes,
        "characters": characters,
        "titles": titles,
        "manuscript_title": manuscript.title,
        "fingerprint": digest([scenes, characters, titles]),
    }


def query(project_id: UUID, manuscript_id: UUID) -> Any:
    return (
        select(Job)
        .where(
            Job.project_id == project_id, Job.target_id == manuscript_id, Job.job_type == JOB_TYPE
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
    )


async def latest(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, *, completed: bool = False
) -> Job | None:
    stmt = query(project_id, manuscript_id)
    if completed:
        stmt = stmt.where(Job.status == "completed")
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()


def progress(memory: dict[str, Any] | None, stage: str = "") -> dict[str, Any]:
    m = memory or {}
    return {
        "processed_scenes": len(m.get("processed_scene_ids", [])),
        "total_scenes": len(m.get("scenes", [])),
        "observations": len(m.get("observations", [])),
        "verified_speakers": len(m.get("speaker_verifications", [])),
        "patterns": len(m.get("patterns", [])),
        "completed_requests": len(m.get("call_cache", {})),
        "stage": stage,
    }


def job_view(job: Job) -> dict[str, Any]:
    saved = job.result or {}
    return {
        "analysis_id": str(job.id),
        "status": job.status,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "progress": saved.get("progress")
        or {**progress(None), "total_scenes": len(job.input["scenes"])},
        "error": {
            "code": job.error.get("code"),
            "message": "분석이 중단됐어요. 이어서 다시 시도할 수 있어요.",
        }
        if job.status == "failed" and job.error
        else None,
        "discovery": saved.get("discovery"),
    }


async def overview(session: AsyncSession, project_id: UUID, manuscript_id: UUID) -> dict[str, Any]:
    current = await source(session, project_id, manuscript_id)
    job = await latest(session, project_id, manuscript_id)
    ready = (
        job
        if job and job.status == "completed"
        else await latest(session, project_id, manuscript_id, completed=True)
    )
    memory = (ready.result or {}).get("memory") if ready else None
    return {
        "manuscript_id": str(manuscript_id),
        "job": job_view(job) if job else None,
        "available_analysis_id": str(ready.id) if ready and memory else None,
        "stale": bool(ready and ready.input["fingerprint"] != current["fingerprint"]),
        "job_stale": bool(job and job.input["fingerprint"] != current["fingerprint"]),
        "mode": memory.get("mode") if memory else None,
        "character_counts": character_counts(memory) if memory else {},
        "chapters": [
            {"order": s["order"], "title": ready.input["titles"][s["scene_id"]]}
            for s in memory["scenes"]
        ]
        if memory and ready
        else [],
        "can_analyze": bool(current["scenes"]),
    }


async def request(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, user_id: UUID
) -> Job:
    data = await source(session, project_id, manuscript_id, lock=True)
    if not data["scenes"]:
        raise errors.ManuscriptTooShort(length=0)
    active = (
        await session.execute(
            query(project_id, manuscript_id).where(Job.status.in_(["queued", "running"])).limit(1)
        )
    ).scalar_one_or_none()
    if active:
        return active
    ready = await latest(session, project_id, manuscript_id, completed=True)
    if (
        ready
        and ready.input["fingerprint"] == data["fingerprint"]
        and ready.input.get("discovery_version") == discovery.VERSION
    ):
        return ready
    job = await jobs.create(
        session,
        project_id=project_id,
        job_type=JOB_TYPE,
        target_type="manuscript",
        target_id=manuscript_id,
        queue="ai",
        created_by=user_id,
        input={**data, "discovery_version": discovery.VERSION},
    )
    job.result = {"discovery": discovery.initial(data)}
    if ready:
        # Changed scene inputs invalidate cache keys inside prolog-ai. No old facts are copied.
        job.result = {
            **job.result,
            "memory": (ready.result or {}).get("memory"),
            "progress": progress(None),
        }
    jobs.announce(session, job)
    await session.flush()
    await session.refresh(job)
    return job


async def retry(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, analysis_id: UUID
) -> Job:
    # Create and retry take the same parent lock, so two live analyses cannot overlap.
    current = await source(session, project_id, manuscript_id, lock=True)
    job = (
        await session.execute(
            query(project_id, manuscript_id).where(Job.id == analysis_id).with_for_update()
        )
    ).scalar_one_or_none()
    if not job:
        raise errors.CharacterMemoryNotFound()
    if job.input["fingerprint"] != current["fingerprint"]:
        raise errors.InvalidInput("원고나 인물이 바뀌었어요. 현재 원고로 새로 분석해 주세요.")
    if job.status != "failed":
        raise errors.InvalidStatusTransition(status=job.status)
    active = (
        await session.execute(
            query(project_id, manuscript_id).where(Job.status.in_(["queued", "running"])).limit(1)
        )
    ).scalar_one_or_none()
    if active:
        raise errors.InvalidStatusTransition(status=active.status)
    retried = await jobs.retry(session, job.id, by_user=True)
    assert retried is not None
    return retried


async def details(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    character_id: UUID,
    through_order: int | None,
) -> dict[str, Any]:
    current = await source(session, project_id, manuscript_id)
    if str(character_id) not in {c["character_id"] for c in current["characters"]}:
        raise errors.CharacterNotFound()
    job = await latest(session, project_id, manuscript_id, completed=True)
    if not job or not job.result:
        raise errors.CharacterMemoryNotReady()
    result = dossier(job.result["memory"], str(character_id), job.input["titles"], through_order)
    result.update(
        analysis_id=str(job.id),
        generated_at=job.finished_at,
        stale=job.input["fingerprint"] != current["fingerprint"],
        manuscript_id=str(manuscript_id),
        manuscript_title=job.input["manuscript_title"],
    )
    return result


async def persist(
    sessions: ai_jobs.Sessions, job_id: UUID, attempt: int, memory: dict[str, Any], stage: str
) -> bool:
    async with sessions() as session:
        job = await discovery.running(session, job_id, attempt)
        if not job:
            return False
        job.result = {**(job.result or {}), "memory": memory, "progress": progress(memory, stage)}
        await session.commit()
        return True


async def handle(sessions: ai_jobs.Sessions, job_id: UUID, ai: AIClient) -> str:
    async with sessions() as session:
        existing = await jobs.get(session, job_id)
        if not existing or existing.job_type != JOB_TYPE:
            return "skipped"
        job = await jobs.claim(session, job_id)
        if not job:
            return "skipped"
        args, attempt = job.input, job.attempt
        previous = (job.result or {}).get("memory")
        report = (job.result or {}).get("discovery")
        project_id = str(job.project_id)
        await session.commit()
    beat = asyncio.create_task(ai_jobs._heartbeat(sessions, job_id, attempt))
    try:
        if report:
            args = await discovery.discover(sessions, job_id, attempt, ai, args, report)
        if not args["characters"]:
            async with sessions() as session:
                job = await discovery.running(session, job_id, attempt)
                if not job:
                    return "lost"
                job.result = {
                    **(job.result or {}),
                    "progress": progress(None, "no_characters"),
                }
                won = await jobs.finish(session, job_id, attempt, "completed")
                await session.commit()
                return "completed" if won else "lost"
        expected_snapshot = snapshot_id(
            project_id,
            [CharacterRef.model_validate(c) for c in args["characters"]],
            [Scene.model_validate(s) for s in args["scenes"]],
        )
        if previous and previous.get("snapshot_id") != expected_snapshot:
            previous = None
        for _ in range(1000):
            try:
                response = await asyncio.to_thread(
                    ai.run_character_memory,
                    project_id,
                    args["scenes"],
                    args["characters"],
                    previous,
                )
            except Exception:
                # Never expose provider exception text or credentials to the polling API.
                response = {"error": {"code": "AI_ANALYSIS_FAILED"}}
            if "data" in response:
                memory = validate_memory(response["data"], require_complete=True).model_dump()
                if memory["snapshot_id"] != expected_snapshot:
                    raise ValueError("memory source mismatch")
                if not await persist(sessions, job_id, attempt, memory, "completed"):
                    return "lost"
                async with sessions() as session:
                    won = await jobs.finish(session, job_id, attempt, "completed")
                    await session.commit()
                return "completed" if won else "lost"
            error = response.get("error") or {}
            extra = error.get("details") or {}
            checkpoint = extra.get("checkpoint")
            if checkpoint:
                checkpoint = validate_memory(checkpoint).model_dump()
                if checkpoint["snapshot_id"] != expected_snapshot:
                    raise ValueError("checkpoint source mismatch")
                if not await persist(sessions, job_id, attempt, checkpoint, extra.get("stage", "")):
                    return "lost"
            if extra.get("internal_code") == "CALL_BUDGET_EXCEEDED" and checkpoint:
                if previous and checkpoint["call_cache"] == previous["call_cache"]:
                    break
                previous = checkpoint
                continue
            async with sessions() as session:
                outcome = await jobs.fail(
                    session,
                    job_id,
                    attempt,
                    {
                        "code": error.get("code", "AI_ANALYSIS_FAILED"),
                        "message": "인물 기억 분석이 중단되었습니다.",
                    },
                    retryable=is_retryable(error),
                )
                await session.commit()
            return outcome or "lost"
        raise ValueError("memory build made no progress or exceeded its call budget")
    except Exception as exc:
        async with sessions() as session:
            outcome = await jobs.fail(
                session,
                job_id,
                attempt,
                {
                    "code": exc.code
                    if isinstance(exc, discovery.DiscoveryFailure)
                    else "AI_ANALYSIS_FAILED",
                    "message": "인물 분석이 중단되었습니다.",
                },
                retryable=isinstance(exc, discovery.DiscoveryFailure)
                and exc.code.endswith("_TIMEOUT"),
            )
            await session.commit()
        return outcome or "lost"
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
