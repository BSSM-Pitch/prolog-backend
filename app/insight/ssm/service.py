"""SSM (Ring 4) — 스토리 구조 지도. 분석은 `structure_analysis` 잡(ai 큐)이다.

패키지 `run_ssm` 이 원고를 "N장" 머리글로 나눠 챕터마다 AI 를 부르고 막 · 노드 · 인과 간선을 합친다.
장편은 한 잡이 수십 분이다 — 워커가 하트비트를 찍어 살아 있는 잡을 좀비로 오판하지 않는다(0011).

**다시 분석(§12-2 결정).** 지도는 원고마다 하나다(`structure_maps.manuscript_id` UNIQUE).
다시 분석하면 막 · 간선 · AI 노드를 새 결과로 바꾸되, **사용자가 고친 노드(`is_user_edited`)는
남긴다** — AI 가 사용자 수정을 덮어쓰지 않는다(ERD). 남긴 노드와 새 노드가 같은 사건을
가리킬 수 있다(병합하지 않는다).
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.client import AIClient, Result
from app.authoring.ass.models import Character
from app.content.manuscripts import service as manuscripts
from app.core import errors
from app.insight.ssm.models import StructureMap, StructureNode, StructureNodeCharacter
from app.insight.ssm.schemas import (
    AnalysisResponse,
    AnalysisStatus,
    NodeResponse,
    NodeUpdate,
    StructureMapResponse,
)
from app.jobs import service as jobs
from app.jobs.models import Job

JOB_TYPE = "structure_analysis"  # 0001 jobs_job_type_chk 의 이름
QUEUE = "ai"


def status_of(job: Job) -> AnalysisStatus:
    return cast(AnalysisStatus, {"running": "analyzing"}.get(job.status, job.status))


def to_analysis(job: Job) -> AnalysisResponse:
    manuscript_id = cast(UUID, job.target_id)
    ref = (
        f"/projects/{job.project_id}/manuscripts/{manuscript_id}/structure-map"
        if job.status == "completed"
        else None
    )
    return AnalysisResponse(
        analysis_id=job.id,
        manuscript_id=manuscript_id,
        status=status_of(job),
        structure_map_ref=ref,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def analysis_error(job: Job) -> dict[str, Any] | None:
    if job.status != "failed" or not job.error:
        return None
    return {
        "code": job.error.get("code", "AI_ANALYSIS_FAILED"),
        "message": job.error.get("message", "AI 분석에 실패했습니다."),
        "details": job.error.get("details") or {},
    }


# --- 분석 작업 -----------------------------------------------------------------


async def request(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, user_id: UUID
) -> Job:
    """본문이 없으면 `MANUSCRIPT_TOO_SHORT`(패키지 기준 1자 — 빈 원고만 거른다)."""
    manuscript = await manuscripts.get(session, project_id, manuscript_id)
    if not (manuscript.content or "").strip():
        raise errors.ManuscriptTooShort(length=0)
    job = await jobs.create(
        session,
        project_id=project_id,
        job_type=JOB_TYPE,
        target_type="manuscript",
        target_id=manuscript_id,
        queue=QUEUE,
        created_by=user_id,
    )
    jobs.announce(session, job)
    await session.flush()
    await session.refresh(job)
    return job


async def _analysis(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    analysis_id: UUID,
    *,
    lock: bool = False,
) -> Job:
    stmt = select(Job).where(
        Job.id == analysis_id,
        Job.project_id == project_id,
        Job.job_type == JOB_TYPE,
        Job.target_id == manuscript_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise errors.StructureAnalysisNotFound()
    return job


async def get_analysis(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, analysis_id: UUID
) -> Job:
    return await _analysis(session, project_id, manuscript_id, analysis_id)


async def retry(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, analysis_id: UUID
) -> Job:
    job = await _analysis(session, project_id, manuscript_id, analysis_id, lock=True)
    if job.status != "failed":
        raise errors.InvalidStatusTransition(status=status_of(job))
    retried = await jobs.retry(session, job.id, by_user=True)
    assert retried is not None
    return retried


# --- 구조 지도 · 노드 ------------------------------------------------------------


async def _map(session: AsyncSession, project_id: UUID, manuscript_id: UUID) -> StructureMap:
    stmt = select(StructureMap).where(
        StructureMap.project_id == project_id, StructureMap.manuscript_id == manuscript_id
    )
    found = (await session.execute(stmt)).scalar_one_or_none()
    if found is None:
        raise errors.StructureMapNotFound()
    return found


async def to_nodes(session: AsyncSession, rows: list[StructureNode]) -> list[NodeResponse]:
    ids = [n.id for n in rows]
    chars: dict[UUID, list[UUID]] = {i: [] for i in ids}
    if ids:
        found = await session.execute(
            select(StructureNodeCharacter.node_id, StructureNodeCharacter.character_id).where(
                StructureNodeCharacter.node_id.in_(ids)
            )
        )
        for node_id, character_id in found:
            chars[node_id].append(character_id)
    return [
        NodeResponse(
            node_id=n.id,
            type=cast(Any, n.node_type),
            chapter=n.chapter_no,
            title=n.title,
            summary=n.summary,
            character_ids=chars[n.id],
            is_user_edited=n.is_user_edited,
        )
        for n in rows
    ]


async def get_map(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID
) -> StructureMapResponse:
    found = await _map(session, project_id, manuscript_id)
    nodes = list(
        (
            await session.execute(
                select(StructureNode)
                .where(StructureNode.map_id == found.id)
                .order_by(StructureNode.chapter_no.nulls_last(), StructureNode.created_at)
            )
        ).scalars()
    )
    return StructureMapResponse(
        manuscript_id=manuscript_id,
        analysis_id=found.job_id,
        acts=found.acts,
        nodes=await to_nodes(session, nodes),
        edges=found.edges,
        generated_at=found.updated_at,
    )


async def _node(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, node_id: UUID
) -> StructureNode:
    found = await _map(session, project_id, manuscript_id)
    stmt = select(StructureNode).where(
        StructureNode.id == node_id, StructureNode.map_id == found.id
    )
    node = (await session.execute(stmt)).scalar_one_or_none()
    if node is None:
        raise errors.StructureNodeNotFound()
    return node


async def get_node(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, node_id: UUID
) -> NodeResponse:
    return (await to_nodes(session, [await _node(session, project_id, manuscript_id, node_id)]))[0]


async def update_node(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, node_id: UUID, body: NodeUpdate
) -> NodeResponse:
    """제목 · 요약을 고치면 `is_user_edited` 가 켜진다 — 다시 분석해도 남는다."""
    node = await _node(session, project_id, manuscript_id, node_id)
    changed = False
    if body.title is not None and body.title != node.title:
        node.title, changed = body.title, True
    if "summary" in body.model_fields_set and body.summary != node.summary:
        node.summary, changed = body.summary, True
    if changed:
        node.is_user_edited = True
    await session.flush()
    await session.refresh(node)
    return (await to_nodes(session, [node]))[0]


# --- 워커 --------------------------------------------------------------------


async def _prepare(session: AsyncSession, job: Job) -> dict[str, Any]:
    manuscript = await manuscripts.get(session, job.project_id, cast(UUID, job.target_id))
    roster = (
        await session.execute(
            select(Character.id, Character.name).where(
                Character.project_id == job.project_id, Character.status == "active"
            )
        )
    ).all()
    return {
        "manuscript_text": manuscript.content or "",
        "characters": [{"character_id": str(i), "name": n} for i, n in roster],
    }


def _call(ai: AIClient, args: Any) -> Result:
    return ai.run_ssm(args["manuscript_text"], args["characters"])


def _uuids(values: list[Any]) -> set[UUID]:
    """패키지가 준 캐릭터 id 중 UUID 인 것만(모르는 id 는 버린다)."""
    out = set()
    for v in values:
        try:
            out.add(UUID(str(v)))
        except ValueError:
            continue
    return out


async def _mapped(session: AsyncSession, job: Job, data: dict[str, Any]) -> None:
    """지도를 새 결과로 바꾼다. 사용자가 고친 노드는 남긴다(머리말)."""
    manuscript_id = cast(UUID, job.target_id)
    found = (
        await session.execute(
            select(StructureMap)
            .where(StructureMap.manuscript_id == manuscript_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if found is None:
        found = StructureMap(project_id=job.project_id, manuscript_id=manuscript_id)
        session.add(found)
        await session.flush()
    else:
        await session.execute(
            delete(StructureNode).where(
                StructureNode.map_id == found.id, StructureNode.is_user_edited.is_(False)
            )
        )

    known = set(
        (
            await session.execute(
                select(Character.id).where(
                    Character.project_id == job.project_id, Character.status == "active"
                )
            )
        ).scalars()
    )
    new_ids: dict[str, UUID] = {}
    for n in data.get("nodes") or []:
        node = StructureNode(
            project_id=job.project_id,
            map_id=found.id,
            node_type=n["type"],
            title=n["title"],
            summary=n.get("summary"),
            chapter_no=n.get("chapter"),
        )
        session.add(node)
        await session.flush()
        new_ids[n["node_id"]] = node.id
        for character_id in _uuids(n.get("character_ids") or []) & known:
            session.add(
                StructureNodeCharacter(
                    node_id=node.id, character_id=character_id, project_id=job.project_id
                )
            )
    found.acts = list(data.get("acts") or [])
    found.edges = [
        {
            "from_node_id": str(new_ids[e["from_node_id"]]),
            "to_node_id": str(new_ids[e["to_node_id"]]),
            "relation": e["relation"],
        }
        for e in data.get("edges") or []
        if e["from_node_id"] in new_ids and e["to_node_id"] in new_ids
    ]
    found.job_id = job.id


async def handle(sessions: Any, job_id: UUID, ai: AIClient) -> ai_jobs.Outcome:
    return await ai_jobs.run(
        sessions, job_id, JOB_TYPE, ai, prepare=_prepare, call=_call, on_success=_mapped
    )
