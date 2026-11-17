"""Durable manuscript-scoped reviews, with revision checks and source-grounded conflicts."""

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.narrative_review import (
    REVIEW_REVISION,
    VERSION,
    Review,
    summary_text,
    validate_evidence,
)
from app.content.manuscripts.models import Chapter, Manuscript
from app.core import errors
from app.insight.scds.models import Conflict, Event
from app.jobs import service as jobs
from app.jobs.models import Job


def fingerprint(chapters: list[dict[str, Any]], rules: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            [VERSION, REVIEW_REVISION, chapters, sorted(rules, key=lambda r: r["rule_id"])],
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


async def snapshot(session: AsyncSession, project_id: UUID, manuscript_id: UUID) -> dict[str, Any]:
    from app.insight.scds.service import rules_payload

    manuscript = await session.scalar(
        select(Manuscript).where(
            Manuscript.id == manuscript_id,
            Manuscript.project_id == project_id,
        )
    )
    if manuscript is None:
        raise errors.ManuscriptNotFound()
    rows = (
        await session.scalars(
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.manuscript_id == manuscript_id,
            )
            .order_by(Chapter.chapter_no, Chapter.id)
        )
    ).all()
    chapters: list[dict[str, Any]] = [
        {
            "chapter_id": str(c.id),
            "chapter_no": c.chapter_no,
            "title": c.title,
            "content": c.content,
        }
        for c in rows
    ]
    if not chapters or not any(c["content"].strip() for c in chapters):
        raise errors.InvalidInput("본문이 있는 원고가 필요합니다.")
    rules = await rules_payload(session, project_id)
    return {
        "manuscript_id": str(manuscript_id),
        "chapters": chapters,
        "saved_rules": rules,
        "source_hash": fingerprint(chapters, rules),
        "chapter_count": len(chapters),
        "char_count": sum(len(c["content"]) for c in chapters),
    }


async def latest(session: AsyncSession, project_id: UUID, manuscript_id: UUID) -> Job | None:
    return await session.scalar(
        select(Job)
        .where(
            Job.project_id == project_id,
            Job.job_type == "conflict_check",
            Job.input["review_mode"].astext == VERSION,
            Job.input["manuscript_id"].astext == str(manuscript_id),
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )


async def create(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, user_id: UUID
) -> Job:
    # Serialize submissions for this manuscript so multiple tabs cannot buy duplicate runs.
    row = await session.scalar(
        select(Manuscript)
        .where(
            Manuscript.id == manuscript_id,
            Manuscript.project_id == project_id,
        )
        .with_for_update()
    )
    if row is None:
        raise errors.ManuscriptNotFound()
    source = await snapshot(session, project_id, manuscript_id)
    existing = await latest(session, project_id, manuscript_id)
    if existing and existing.input.get("source_hash") == source["source_hash"]:
        if existing.status in {"queued", "running", "completed"}:
            return existing
    first = source["chapters"][0]
    event = Event(
        project_id=project_id,
        chapter_id=UUID(first["chapter_id"]),
        description=first["content"],
        created_by=user_id,
    )
    session.add(event)
    await session.flush()
    job = await jobs.create(
        session,
        project_id=project_id,
        job_type="conflict_check",
        target_type="event",
        target_id=event.id,
        queue="ai",
        created_by=user_id,
        input={
            **source,
            "review_mode": VERSION,
            "rule_result": {
                "has_candidate": False,
                "skipped": False,
                "skipped_reason": None,
                "candidates": [],
            },
        },
    )
    jobs.announce(session, job)
    await session.flush()
    await session.refresh(job)
    return job


async def is_stale(session: AsyncSession, job: Job) -> bool:
    try:
        current = await snapshot(session, job.project_id, UUID(job.input["manuscript_id"]))
    except (errors.ManuscriptNotFound, errors.InvalidInput):
        return True
    return current["source_hash"] != job.input["source_hash"]


async def result_view(session: AsyncSession, job: Job) -> dict[str, Any]:
    data = job.result or {}
    return {
        "manuscript_id": job.input["manuscript_id"],
        "source_hash": job.input["source_hash"],
        "chapter_count": job.input["chapter_count"],
        "char_count": job.input["char_count"],
        "stale": await is_stale(session, job),
        # Apply the same presentation to saved reviews without buying a new model run.
        "summary": summary_text(data.get("tracks", []), data.get("findings", []))
        if job.status == "completed"
        else "",
        "tracks": data.get("tracks", []),
        "findings": [{k: v for k, v in f.items() if k != "key"} for f in data.get("findings", [])],
        "more_items": data.get("more_items", False),
    }


async def save(session: AsyncSession, job: Job, data: dict[str, Any]) -> None:
    from app.insight.scds.service import _suppressed

    review = Review.model_validate({k: data[k] for k in Review.model_fields})
    validate_evidence(review, job.input["chapters"])
    # Serialize review publication; all reads also recheck the complete source revision.
    await session.scalar(
        select(Manuscript)
        .where(
            Manuscript.id == UUID(job.input["manuscript_id"]),
        )
        .with_for_update()
    )
    if await is_stale(session, job):
        job.result = {**data, "stale": True}
        return
    chapters = {c["chapter_id"]: c for c in job.input["chapters"]}
    findings = []
    for finding in review.findings:
        f = finding.model_dump()
        key = hashlib.sha256(
            json.dumps(
                [job.input["manuscript_id"], job.input["source_hash"], f["evidence"]],
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        f["key"] = key
        findings.append(f)
        if await _suppressed(session, job.project_id, [key]):
            continue
        # Explanation gaps remain review notes, not definite rule violations.
        if finding.category != "contradiction":
            continue
        anchor = finding.evidence[-1]
        chapter = chapters[anchor.chapter_id]
        event = Event(
            project_id=job.project_id,
            chapter_id=UUID(anchor.chapter_id),
            description=chapter["content"],
            created_by=job.created_by,
        )
        session.add(event)
        await session.flush()
        session.add(
            Conflict(
                project_id=job.project_id,
                event_id=event.id,
                chapter_id=UUID(anchor.chapter_id),
                character_id=None,
                rule_id=None,
                detected_by="ai",
                severity=finding.severity,
                advice=finding.reason,
                conflict_target=finding.title,
                matched_keyword=anchor.quote,
                suppression_key=key,
                job_id=job.id,
            )
        )
    job.result = {**data, "findings": findings, "stale": False}
