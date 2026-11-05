"""Checkpointed discovery and fenced, idempotent registration before memory extraction."""

import asyncio
from copy import deepcopy
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.character_discovery import verified
from app.ai.client import AIClient
from app.authoring.ass.models import Character, CharacterEditHistory
from app.core import errors
from app.jobs.models import Job

VERSION = 1
CHUNK_CHARS = 12000


def chunks(args: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "scene_id": scene["scene_id"],
            "revision": scene["revision"],
            "start": start,
            "text": scene["text"][start : start + CHUNK_CHARS],
        }
        for scene in args["scenes"]
        for start in range(0, len(scene["text"]), CHUNK_CHARS - 300)
    ]


def initial(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": VERSION,
        "processed_chunks": 0,
        "total_chunks": len(chunks(args)),
        "complete": False,
        "candidates": [],
    }


async def running(session: AsyncSession, job_id: UUID, attempt: int) -> Job | None:
    return await session.scalar(
        select(Job)
        .where(Job.id == job_id, Job.status == "running", Job.attempt == attempt)
        .with_for_update()
    )


async def lock_name(session: AsyncSession, project_id: UUID, name: str) -> None:
    # Same lock key as ASS's manual confirm, so manual and automatic creation serialize.
    key = func.hashtextextended(
        func.concat("character:", str(project_id), ":", func.lower(name)), 0
    )
    await session.execute(select(func.pg_advisory_xact_lock(key)))


async def people(session: AsyncSession, project_id: UUID) -> list[Character]:
    return list(
        await session.scalars(
            select(Character)
            .where(Character.project_id == project_id, Character.status == "active")
            .order_by(Character.id)
        )
    )


def matching(characters: list[Character], name: str) -> list[Character]:
    return [
        c
        for c in characters
        if name.casefold() in {n.casefold() for n in [c.name, *(c.aliases or [])]}
    ]


async def create_person(
    session: AsyncSession, job: Job, candidate: dict[str, Any], user_id: UUID | None = None
) -> Character:
    character = Character(project_id=job.project_id, name=candidate["name"])
    session.add(character)
    await session.flush()
    session.add(
        CharacterEditHistory(
            project_id=job.project_id,
            character_id=character.id,
            edited_by=user_id or job.created_by,
            phase="confirmed",
            action="create",
            field="name",
            after_value=character.name,
        )
    )
    return character


async def register(session: AsyncSession, job: Job, report: dict[str, Any]) -> None:
    candidates = report["candidates"]
    for name in sorted({c["name"] for c in candidates}, key=str.casefold):
        await lock_name(session, job.project_id, name)
    characters = await people(session, job.project_id)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate["name"].casefold(), []).append(candidate)
    all_names = set(grouped) | {c.name.casefold() for c in characters}
    for group in grouped.values():
        candidate = group[0]
        found = matching(characters, candidate["name"])
        name_key = candidate["name"].casefold()
        longer_names = [n for n in all_names if n != name_key and n.endswith(name_key)]
        status = "needs_review"
        character = None
        if len(found) == 1 and not any(
            c.get("alias_of") and c["alias_of"].casefold() != found[0].name.casefold()
            for c in group
        ):
            character, status = found[0], "existing"
        elif (
            not found
            and not longer_names
            and all(c["kind"] == "named_person" and not c["alias_of"] for c in group)
        ):
            character = await create_person(session, job, candidate)
            characters.append(character)
            status = "added"
        for item in group:
            item.update(status=status, character_id=str(character.id) if character else None)


class DiscoveryFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


async def discover(
    sessions: ai_jobs.Sessions,
    job_id: UUID,
    attempt: int,
    ai: AIClient,
    args: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Every chunk is durable; final registration and the new memory snapshot are atomic."""
    from app.insight.character_memory import service

    if report["complete"]:
        return args
    for index, chunk in enumerate(chunks(args)):
        if index < report["processed_chunks"]:
            continue
        known = [
            {"name": c["name"], "aliases": c.get("aliases", [])} for c in args["characters"]
        ] + [{"name": c["name"]} for c in report["candidates"]]
        known = list({c["name"].casefold(): c for c in reversed(known)}.values())
        payload = {**chunk, "known_people": known}
        response = await asyncio.to_thread(ai.run_character_discovery, payload)
        if "data" not in response:
            raise DiscoveryFailure((response.get("error") or {}).get("code", "AI_DISCOVERY_FAILED"))
        candidates = verified(payload, response["data"])
        seen = {c["candidate_id"] for c in report["candidates"]}
        for candidate in candidates:
            key = sha256(repr([candidate["name"], candidate["evidence"]]).encode()).hexdigest()[:24]
            if key not in seen:
                report["candidates"].append(
                    {
                        **candidate,
                        "candidate_id": key,
                        "status": "pending",
                        "character_id": None,
                    }
                )
                seen.add(key)
        report["processed_chunks"] = index + 1
        async with sessions() as session:
            job = await running(session, job_id, attempt)
            if not job:
                raise DiscoveryFailure("DISCOVERY_ATTEMPT_LOST")
            job.result = {**(job.result or {}), "discovery": report}
            await session.commit()
    async with sessions() as session:
        # Acquire the same manuscript lock as request/retry, before locking the job.
        current = await service.source(
            session, UUID(args["project_id"]), UUID(args["manuscript_id"]), lock=True
        )
        job = await running(session, job_id, attempt)
        if not job:
            raise DiscoveryFailure("DISCOVERY_ATTEMPT_LOST")
        if current["scenes"] != args["scenes"] or current["titles"] != args["titles"]:
            raise DiscoveryFailure("DISCOVERY_SOURCE_CHANGED")
        await register(session, job, report)
        current = await service.source(session, job.project_id, UUID(args["manuscript_id"]))
        updated = {**args, **current, "discovery_version": VERSION}
        job.input = updated
        report["complete"] = True
        job.result = {**(job.result or {}), "discovery": report}
        await session.commit()
        return updated


async def resolve(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    job_id: UUID,
    candidate_id: str,
    action: str,
    character_id: UUID | None,
    user_id: UUID,
    name: str | None = None,
) -> dict[str, Any]:
    from app.insight.character_memory import service

    current = await service.source(session, project_id, manuscript_id, lock=True)
    job = await session.scalar(
        select(Job)
        .where(
            Job.id == job_id,
            Job.project_id == project_id,
            Job.target_id == manuscript_id,
            Job.job_type == service.JOB_TYPE,
        )
        .with_for_update()
    )
    if not job:
        raise errors.CharacterMemoryNotFound()
    if current["scenes"] != job.input["scenes"]:
        raise errors.InvalidInput("원고가 바뀌었어요. 현재 원고로 분석한 뒤 후보를 확인해 주세요.")
    report = deepcopy((job.result or {}).get("discovery"))
    if not report or not report["complete"] or job.status in ("queued", "running"):
        raise errors.InvalidInput("분석이 끝난 뒤 인물 후보를 확인해 주세요.")
    candidate = next((c for c in report["candidates"] if c["candidate_id"] == candidate_id), None)
    if not candidate:
        raise errors.InvalidInput("인물 후보를 찾을 수 없어요.")
    if candidate["status"] != "needs_review":
        return candidate
    registration_name = name.strip() if name is not None else candidate["name"]
    if not registration_name:
        raise errors.InvalidInput("등록할 인물 이름을 입력해 주세요.")
    for label in sorted({candidate["name"], registration_name}, key=str.casefold):
        await lock_name(session, project_id, label)
    chars = await people(session, project_id)
    found = matching(chars, candidate["name"])
    target = None
    if action == "link":
        target = next((c for c in chars if c.id == character_id), None)
        if not target:
            raise errors.CharacterNotFound()
        if candidate["name"].casefold() != target.name.casefold() and any(
            c.id != target.id for c in found
        ):
            raise errors.InvalidInput(
                "이 표기는 다른 인물에도 사용되고 있어요. 동일인인지 먼저 확인해 주세요."
            )
        if candidate["name"].casefold() != target.name.casefold():
            target.aliases = sorted(set([*(target.aliases or []), candidate["name"]]))
            session.add(
                CharacterEditHistory(
                    project_id=project_id,
                    character_id=target.id,
                    edited_by=user_id,
                    phase="confirmed",
                    action="update",
                    field="aliases",
                    after_value=candidate["name"],
                )
            )
    elif action == "create":
        found = matching(chars, registration_name)
        if len(found) > 1:
            raise errors.InvalidInput(
                "같은 이름의 인물이 여럿 있어요. 연결할 인물을 선택해 주세요."
            )
        target = (
            found[0]
            if found
            else await create_person(
                session, job, {**candidate, "name": registration_name}, user_id
            )
        )
        if registration_name.casefold() != candidate["name"].casefold():
            if any(c.id != target.id for c in matching(chars, candidate["name"])):
                raise errors.InvalidInput(
                    "원래 표기가 다른 인물에 연결돼 있어요. 연결 대상을 확인해 주세요."
                )
            target.aliases = sorted(set([*(target.aliases or []), candidate["name"]]))
            session.add(
                CharacterEditHistory(
                    project_id=project_id,
                    character_id=target.id,
                    edited_by=user_id,
                    phase="confirmed",
                    action="update",
                    field="aliases",
                    after_value=candidate["name"],
                )
            )
    for item in report["candidates"]:
        if item["name"].casefold() == candidate["name"].casefold():
            item.update(
                status="dismissed" if action == "dismiss" else "confirmed",
                character_id=str(target.id) if target else None,
            )
    job.result = {**(job.result or {}), "discovery": report}
    await session.flush()
    return candidate
