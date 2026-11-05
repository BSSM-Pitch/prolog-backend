"""Evidence counts, temporal boundaries, authorization, and durable worker resumes."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from prolog_ai.memory.schema import Character, Memory, Scene
from prolog_ai.memory.source import snapshot_id, validate_memory
from sqlalchemy import update

from app.db.session import SessionFactory
from app.insight.character_memory import service
from app.insight.character_memory.projection import dossier, situation_metrics
from app.insight.character_memory.schemas import DetailsResponse
from app.jobs import service as jobs
from app.jobs.models import Job
from tests.conftest import FakeAI, code, signup
from tests.test_ssm import _manuscript


def memory_for(project: str, scenes: list[dict], people: list[dict]) -> dict[str, Any]:
    chars = sorted([Character.model_validate(c) for c in people], key=lambda c: c.character_id)
    sources = sorted([Scene.model_validate(s) for s in scenes], key=lambda s: s.order)
    return Memory(
        project_id=project,
        snapshot_id=snapshot_id(project, chars, sources),
        analysis_key="test-analysis",
        model="test-model",
        mode="real",
        characters=chars,
        scenes=sources,
        processed_scene_ids=[s.scene_id for s in sources],
        complete=True,
    ).model_dump()


def fixture_memory() -> dict[str, Any]:
    m = memory_for(
        "p",
        [
            {
                "scene_id": f"s{i}",
                "order": i,
                "revision": "1",
                "text": '🌙 그는 "괜찮아." 했다. 손이 떨렸다.',
            }
            for i in range(1, 7)
        ],
        [{"character_id": "a", "name": "서진"}, {"character_id": "b", "name": "동료"}],
    )
    for s in m["scenes"]:
        i = s["order"]
        start = s["text"].index("괜찮아.")
        m["observations"].append(
            {
                "observation_id": f"o{i}",
                "character_id": "a",
                "attribution": "certain",
                "kind": "speech",
                "presentation": "depicted",
                "behavior_role": "choice" if i in [1, 2, 3, 4] else "involuntary_reaction",
                "summary": "짧은 대답",
                "context": "동료와 대화",
                "interlocutor_ids": ["b"],
                "evidence": {
                    "scene_id": s["scene_id"],
                    "revision": "1",
                    "start": start,
                    "end": start + 4,
                    "quote": "괜찮아.",
                },
            }
        )
    uncertain = deepcopy(m["observations"][0])
    uncertain.update(observation_id="uncertain", attribution="uncertain", character_id=None)
    m["observations"].append(uncertain)
    for o in m["observations"]:
        m["speaker_verifications"].append(
            {
                "observation_id": o["observation_id"],
                "original_observation_id": o["observation_id"],
                "original_character_id": "a",
                "original_attribution": "certain",
                "reason": "발화 지시문 확인",
                "evidence": o["evidence"],
            }
        )
    m["speaker_verification_version"] = "v1"
    m["patterns"] = [
        {
            "pattern_id": "p1",
            "character_id": "a",
            "description": "짧게 답함",
            "condition": "대화",
            "supporting_observation_ids": ["o1", "o2"],
            "counterexample_observation_ids": ["o6"],
            "supporting_scene_count": 2,
            "counterexample_scene_count": 1,
            "evaluated_observation_count": 6,
            "analyzed_through_order": 6,
            "status": "contested",
        }
    ]
    m["hypotheses"] = [
        {
            "hypothesis_id": "h",
            "character_id": "a",
            "pattern_id": "p1",
            "statement": "부담일 수 있음",
            "alternatives": ["말을 아끼는 습관"],
        }
    ]
    return validate_memory(m, require_complete=True).model_dump()


def test_projection_exact_unicode_attribution_and_cutoff() -> None:
    m = fixture_memory()
    d = dossier(m, "a", {}, None)
    assert d["counts"]["observations"] == 6
    assert d["counts"]["uncertain"] == 1
    assert d["patterns"][0]["counterexample_observation_ids"] == ["o6"]
    assert d["hypotheses"][0]["alternatives"] == ["말을 아끼는 습관"]
    row = d["observations"][0]
    assert (
        row["source"]["before"] + row["source"]["quote"] + row["source"]["after"]
        == m["scenes"][0]["text"]
    )
    assert row["source"]["quote"] == row["evidence"]["quote"]
    past = dossier(m, "a", {}, 3)
    assert past["counts"]["observations"] == 3
    assert past["patterns"] == past["hypotheses"] == []
    assert past["coverage"]["patterns_hidden_by_cutoff"] == 1
    assert all(t["order"] <= 3 for t in past["metrics"]["timeline"])
    assert dossier(m, "b", {}, None)["counts"]["observations"] == 0


def test_metrics_denominators_deduplication_and_missing_data() -> None:
    d = dossier(fixture_memory(), "a", {}, None)
    metrics = situation_metrics(d["observations"] + [d["observations"][-1]], list(range(1, 7)), "a")
    assert metrics["recent"]["record_count"] == 3  # overlap is not another occurrence
    assert metrics["recent"]["choice_share"] == 33.3
    assert metrics["previous"]["choice_share"] == 100.0
    assert metrics["choice_delta_pp"] == -66.7
    assert metrics["recent"]["partner_counts"] == {"b": 3}
    assert metrics["recent"]["small_sample"]
    empty = dossier(fixture_memory(), "b", {}, None)["metrics"]
    assert empty["recent"]["choice_share"] is None
    assert empty["choice_delta_pp"] is None
    assert empty["recent"]["record_count"] == 0
    assert dossier(fixture_memory(), "a", {}, 5)["metrics"]["choice_delta_pp"] is None


def test_populated_response_preserves_provenance_and_alternatives() -> None:
    value = dossier(fixture_memory(), "a", {}, None)
    value.update(
        analysis_id="analysis",
        generated_at=datetime.now(UTC),
        stale=False,
        manuscript_id="manuscript",
        manuscript_title="원고",
    )
    result = DetailsResponse.model_validate(value).model_dump()
    counter_id = result["patterns"][0]["counterexample_observation_ids"][0]
    counter = next(o for o in result["observations"] if o["observation_id"] == counter_id)
    assert counter["source"]["quote"] == "괜찮아."
    assert result["uncertain_observations"][0]["speaker_check"]["reason"] == "발화 지시문 확인"
    assert result["hypotheses"][0]["status"] == "tentative"


async def start(client: AsyncClient, m: dict) -> tuple[UUID, dict]:
    response = await client.post(f"{m['base']}/character-memory", headers=m["h"])
    assert response.status_code == 202, response.text
    aid = UUID(response.json()["data"]["analysis_id"])
    async with SessionFactory() as session:
        job = await jobs.get(session, aid)
        assert job
        memory = memory_for(str(job.project_id), job.input["scenes"], job.input["characters"])
    return aid, memory


async def test_api_dedup_stale_and_retry(client: AsyncClient) -> None:
    m = await _manuscript(client, "memory@example.com")
    base, h = m["base"], m["h"]
    detail = f"{base}/characters/{m['char']}/details"
    assert code(await client.get(detail, headers=h)) == "CHARACTER_MEMORY_NOT_READY"
    aid, memory = await start(client, m)
    assert (await start(client, m))[0] == aid
    assert await service.handle(SessionFactory, aid, FakeAI({"data": memory})) == "completed"
    assert (await start(client, m))[0] == aid  # cached complete result
    got = (await client.get(detail, headers=h)).json()["data"]
    assert not got["stale"] and got["metrics"]["recent"]["choice_share"] is None
    assert (await client.get(detail + "?through_order=-1", headers=h)).status_code == 400
    await client.patch(base, json={"content": "새로운 본문"}, headers=h)
    assert (await client.get(detail, headers=h)).json()["data"]["stale"]
    newer, _ = await start(client, m)
    assert newer != aid
    assert (
        await service.handle(
            SessionFactory,
            newer,
            FakeAI({"error": {"code": "AI_ANALYSIS_FAILED", "message": "private provider text"}}),
        )
        == "failed"
    )
    overview = (await client.get(f"{base}/character-memory", headers=h)).json()["data"]
    assert overview["available_analysis_id"] == str(aid)
    assert "private provider" not in str(overview)
    assert "call_cache" not in str(overview) and "scenes" not in overview
    assert (
        await client.post(f"{base}/character-memory/{newer}/retry", headers=h)
    ).status_code == 202
    assert (
        await client.post(f"{base}/character-memory/{newer}/retry", headers=h)
    ).status_code == 409


async def test_resumes_checkpoint_in_slices_and_keeps_it_on_failure(client: AsyncClient) -> None:
    m = await _manuscript(client, "resume@example.com")
    aid, memory = await start(client, m)
    checkpoint = deepcopy(memory)
    checkpoint.update(complete=False, call_cache={"chunk": {"observations": []}})
    budget = {
        "error": {
            "code": "AI_ANALYSIS_FAILED",
            "details": {
                "internal_code": "CALL_BUDGET_EXCEEDED",
                "checkpoint": checkpoint,
                "stage": "extraction",
            },
        }
    }
    ai = FakeAI(budget, {"error": {"code": "AI_ANALYSIS_FAILED"}})
    assert await service.handle(SessionFactory, aid, ai) == "failed"
    assert [c for c in ai.calls if c[0] == "run_character_memory"][1][1][3] == checkpoint
    async with SessionFactory() as session:
        saved = await jobs.get(session, aid)
        assert saved and saved.result and saved.result["memory"] == checkpoint
    assert (
        await client.post(f"{m['base']}/character-memory/{aid}/retry", headers=m["h"])
    ).status_code == 202
    resumed = FakeAI({"data": memory})
    assert await service.handle(SessionFactory, aid, resumed) == "completed"
    assert resumed.calls[0][1][3] == checkpoint


async def test_worker_fencing_and_source_validation(client: AsyncClient) -> None:
    m = await _manuscript(client, "wrong-source@example.com")
    aid, memory = await start(client, m)
    wrong = memory_for(
        memory["project_id"], [{**memory["scenes"][0], "text": "다른 원문"}], memory["characters"]
    )
    assert await service.handle(SessionFactory, aid, FakeAI({"data": wrong})) == "failed"
    assert not await service.persist(SessionFactory, aid, 1, memory, "completed")
    async with SessionFactory() as session:
        await session.execute(update(Job).where(Job.id == aid).values(status="running", attempt=2))
        await session.commit()
    assert not await service.persist(SessionFactory, aid, 1, memory, "completed")


async def test_project_and_manuscript_boundaries_and_viewer(client: AsyncClient) -> None:
    m = await _manuscript(client, "owner-memory@example.com")
    other = await _manuscript(client, "other-memory@example.com")
    base, h = m["base"], m["h"]
    assert (await client.get(f"{base}/character-memory", headers=other["h"])).status_code == 403
    assert (
        await client.get(f"{base}/characters/{other['char']}/details", headers=h)
    ).status_code == 404
    missing = base.rsplit("/", 1)[0] + f"/{UUID(int=1)}/character-memory"
    assert (await client.get(missing, headers=h)).status_code == 404
    assert (
        await client.post(f"{base}/character-memory/{UUID(int=1)}/retry", headers=h)
    ).status_code == 404
    viewer = await signup(client, "view-memory@example.com")
    project = base.split("/manuscripts")[0]
    invitation = (
        await client.post(
            f"{project}/invitations",
            json={"invited_email": viewer["email"], "role": "viewer"},
            headers=h,
        )
    ).json()["data"]
    await client.post(
        f"{project}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=viewer["headers"],
    )
    assert (
        await client.get(f"{base}/character-memory", headers=viewer["headers"])
    ).status_code == 200
    assert (
        await client.post(f"{base}/character-memory", headers=viewer["headers"])
    ).status_code == 403
    assert (
        await client.post(f"{base}/character-memory/{UUID(int=1)}/retry", headers=viewer["headers"])
    ).status_code == 403


async def test_retry_cannot_revive_an_old_failed_job_alongside_active(client: AsyncClient) -> None:
    m = await _manuscript(client, "retry-race@example.com")
    aid, _ = await start(client, m)
    assert (
        await service.handle(SessionFactory, aid, FakeAI({"error": {"code": "AI_ANALYSIS_FAILED"}}))
        == "failed"
    )
    newer, _ = await start(client, m)
    assert newer != aid
    assert (
        await client.post(f"{m['base']}/character-memory/{aid}/retry", headers=m["h"])
    ).status_code == 409


@pytest.mark.parametrize("content", ["", "   "])
async def test_empty_source_is_rejected(client: AsyncClient, content: str) -> None:
    m = await _manuscript(client, "empty-memory@example.com", content=content)
    assert (
        code(await client.post(f"{m['base']}/character-memory", headers=m["h"]))
        == "MANUSCRIPT_TOO_SHORT"
    )
