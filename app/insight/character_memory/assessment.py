"""인물 평가를 원문 스냅샷·평가 기준·독서 시점에 묶어 저장한다."""

import json
import logging
from collections import defaultdict
from statistics import median
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.character_assessment import (
    RUBRIC,
    RUBRIC_VERSION,
    AssessmentDraft,
    InvalidAssessmentEvidence,
    validate_axis_references,
)
from app.ai.client import AIClient
from app.core import errors
from app.insight.character_memory import service
from app.insight.character_memory.projection import dossier
from app.jobs import service as jobs
from app.jobs.models import Job

JOB_TYPE = "character_assessment"
MAX_RECORDS = 180
MAX_SOURCE_CHARS = 120000
MIN_RATED_SCENES = 3
log = logging.getLogger(__name__)


def evaluation_source(data: dict[str, Any]) -> dict[str, Any]:
    """각 장면을 고르게 읽는다. 반례·행동 역할을 우선하되 표본 범위를 숨기지 않는다."""
    records = data["observations"]
    counter_ids = {i for p in data["patterns"] for i in p["counterexample_observation_ids"]}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[r["evidence"]["scene_id"]].append(r)
    queues = []
    for group in groups.values():
        preferred = [r for r in group if r["observation_id"] in counter_ids]
        for role in ("choice", "involuntary_reaction", "awareness", "desire", "other"):
            options = [r for r in group if r["behavior_role"] == role and r not in preferred]
            if options:
                preferred.extend([options[0], options[-1]])
        # Spread remaining positions across the scene instead of taking just its opening.
        remaining = [r for r in group if r not in preferred]
        middle = []
        while remaining:
            middle.append(remaining.pop(len(remaining) // 2))
        ordered = {r["observation_id"]: r for r in preferred + middle}
        queues.append(list(ordered.values()))
    if len(queues) > MAX_RECORDS:
        # Very long books must not lose the ending just because the record budget is full.
        indexes = (
            [len(queues) // 2]
            if MAX_RECORDS == 1
            else [round(i * (len(queues) - 1) / (MAX_RECORDS - 1)) for i in range(MAX_RECORDS)]
        )
        queues = [queues[i] for i in indexes]
    selected: list[dict[str, Any]] = []
    size = 0
    while any(queues) and len(selected) < MAX_RECORDS:
        for queue in queues:
            if not queue or len(selected) >= MAX_RECORDS:
                continue
            r = queue.pop(0)
            row = {
                "observation_id": r["observation_id"],
                "scene_id": r["evidence"]["scene_id"],
                "order": r["order"],
                "kind": r["kind"],
                "presentation": r["presentation"],
                "behavior_role": r["behavior_role"],
                "summary": r["summary"],
                "context": r["context"],
                "quote": r["evidence"]["quote"],
                "before": r["source"]["before"][-80:],
                "after": r["source"]["after"][:80],
            }
            length = len(json.dumps(row, ensure_ascii=False))
            if size + length > MAX_SOURCE_CHARS:
                continue
            selected.append(row)
            size += length
    return {
        "character_id": data["character_id"],
        "character_name": data["character_name"],
        "through_order": data["through_order"],
        "records": selected,
        "coverage": {
            "available_records": len(records),
            "reviewed_records": len(selected),
            "available_scenes": len(groups),
            "reviewed_scenes": len({r["scene_id"] for r in selected}),
            "sampling": "scene_balanced_with_counterevidence",
            "exhaustive": len(selected) == len(records),
        },
    }


def calculate(raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    parsed = AssessmentDraft.model_validate(raw)
    by_axis = {a.axis_id: a for a in parsed.axes}
    if len(by_axis) != len(RUBRIC) or set(by_axis) != {r["id"] for r in RUBRIC}:
        raise InvalidAssessmentEvidence("missing or duplicate rubric axis")
    axes = []
    for rubric in RUBRIC:
        axis = by_axis[rubric["id"]]
        verified = True
        try:
            validate_axis_references(axis, payload)
        except InvalidAssessmentEvidence as exc:
            log.warning("Withheld assessment axis: %s", exc)
            verified = False
            axis = axis.model_copy(
                update={
                    "verdict": "평가에 사용한 원문 근거를 확인하지 못해 점수를 보류합니다.",
                    "conditions": "이 항목은 근거를 다시 검토해야 합니다.",
                    "limitations": (
                        "모델의 인용 또는 구간 집계가 검증을 통과하지 못했습니다. "
                        "낮은 능력이나 성향을 뜻하지 않습니다."
                    ),
                    "ratings": [],
                    "counterevidence_ids": [],
                }
            )
        scene_ids = {r.scene_id for r in axis.ratings}
        enough = len(scene_ids) >= MIN_RATED_SCENES
        levels = [r.level for r in axis.ratings]
        axes.append(
            {
                **axis.model_dump(),
                "rubric": rubric,
                "status": "unverified_evidence"
                if not verified
                else "scored"
                if enough
                else "insufficient_evidence",
                "score": median(levels) if enough else None,
                "observed_range": [min(levels), max(levels)] if levels else None,
                "rated_scenes": len(scene_ids),
            }
        )
    return {
        "rubric_version": RUBRIC_VERSION,
        "axes": axes,
        "coverage": payload["coverage"],
        "minimum_rated_scenes": MIN_RATED_SCENES,
        "aggregation": "median_of_unique_scene_ratings",
    }


def insufficient(payload: dict[str, Any]) -> dict[str, Any]:
    return calculate(
        {
            "axes": [
                {
                    "axis_id": r["id"],
                    "verdict": "판단할 원문 근거가 부족합니다.",
                    "conditions": "등록된 인물의 확실한 관찰이 여러 장면에서 필요합니다.",
                    "ratings": [],
                    "counterevidence_ids": [],
                    "limitations": "기록 부족은 낮은 능력이나 성향을 의미하지 않습니다.",
                }
                for r in RUBRIC
            ]
        },
        payload,
    )


async def scope(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    character_id: UUID,
    through_order: int | None,
    *,
    lock: bool = False,
) -> tuple[Job, bool]:
    current = await service.source(session, project_id, manuscript_id, lock=lock)
    if str(character_id) not in {c["character_id"] for c in current["characters"]}:
        raise errors.CharacterNotFound()
    memory = await service.latest(session, project_id, manuscript_id, completed=True)
    if not memory or not memory.result:
        raise errors.CharacterMemoryNotReady()
    return memory, memory.input["fingerprint"] != current["fingerprint"]


def query(
    project_id: UUID, manuscript_id: UUID, character_id: UUID, memory: Job, cutoff: int | None
) -> Any:
    return (
        select(Job)
        .where(
            Job.project_id == project_id,
            Job.target_id == character_id,
            Job.job_type == JOB_TYPE,
            Job.input["manuscript_id"].as_string() == str(manuscript_id),
            Job.input["memory_analysis_id"].as_string() == str(memory.id),
            Job.input["cutoff_key"].as_string() == str(cutoff),
            Job.input["rubric_version"].as_string() == RUBRIC_VERSION,
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
    )


def job_view(job: Job) -> dict[str, Any]:
    return {
        "assessment_id": str(job.id),
        "status": job.status,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "result": job.result if job.status == "completed" else None,
        "error": "평가를 완료하지 못했습니다. 다시 시도해 주세요."
        if job.status == "failed"
        else None,
    }


async def overview(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    character_id: UUID,
    through_order: int | None,
) -> dict[str, Any]:
    memory, stale = await scope(session, project_id, manuscript_id, character_id, through_order)
    job = (
        await session.execute(
            query(project_id, manuscript_id, character_id, memory, through_order).limit(1)
        )
    ).scalar_one_or_none()
    return {
        "rubric_version": RUBRIC_VERSION,
        "rubric": RUBRIC,
        "stale": stale,
        "memory_analysis_id": str(memory.id),
        "job": job_view(job) if job else None,
    }


async def request(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    character_id: UUID,
    user_id: UUID,
    through_order: int | None,
) -> dict[str, Any]:
    memory, stale = await scope(
        session, project_id, manuscript_id, character_id, through_order, lock=True
    )
    if stale:
        raise errors.InvalidInput("원고나 인물이 바뀌었습니다. 인물 기억을 먼저 갱신해 주세요.")
    old = (
        await session.execute(
            query(project_id, manuscript_id, character_id, memory, through_order).limit(1)
        )
    ).scalar_one_or_none()
    if old and (
        old.status in ("queued", "running")
        or (
            old.status == "completed"
            and not any(
                a["status"] == "unverified_evidence" for a in (old.result or {}).get("axes", [])
            )
        )
    ):
        return job_view(old)
    assert memory.result is not None
    data = dossier(
        memory.result["memory"], str(character_id), memory.input["titles"], through_order
    )
    payload = evaluation_source(data)
    job = await jobs.create(
        session,
        project_id=project_id,
        job_type=JOB_TYPE,
        target_type="character",
        target_id=character_id,
        queue="ai",
        created_by=user_id,
        input={
            "manuscript_id": str(manuscript_id),
            "memory_analysis_id": str(memory.id),
            "snapshot_id": data["snapshot_id"],
            "cutoff_key": str(through_order),
            "rubric_version": RUBRIC_VERSION,
            "payload": payload,
        },
    )
    jobs.announce(session, job)
    await session.flush()
    await session.refresh(job)
    return job_view(job)


async def prepare(session: AsyncSession, job: Job) -> dict[str, Any]:
    # The immutable source subset is already captured at request time.
    return job.input["payload"]


def call(ai: AIClient, payload: dict[str, Any]) -> dict[str, Any]:
    if payload["coverage"]["reviewed_scenes"] < MIN_RATED_SCENES:
        return {"data": insufficient(payload), "meta": {"review_passes": 0}}
    try:
        response = ai.run_character_assessment(payload)
        if "error" in response:
            return {
                "error": {"code": (response.get("error") or {}).get("code", "AI_ANALYSIS_FAILED")}
            }
        return {"data": calculate(response["data"], payload), "meta": response.get("meta", {})}
    except InvalidAssessmentEvidence as exc:
        log.warning("Invalid assessment evidence: %s", exc)
        return {"error": {"code": "ASSESSMENT_INVALID_EVIDENCE"}}
    except Exception:
        log.warning("Assessment failed source or structure validation")
        return {"error": {"code": "AI_ANALYSIS_FAILED"}}


async def handle(sessions: ai_jobs.Sessions, job_id: UUID, ai: AIClient) -> str:
    return await ai_jobs.run(sessions, job_id, JOB_TYPE, ai, prepare=prepare, call=call)
