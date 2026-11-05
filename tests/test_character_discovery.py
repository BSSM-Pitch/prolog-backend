"""Empty casts, exact evidence, alias review, restart durability, and competing registration."""

import asyncio
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.ai.character_discovery import verified
from app.authoring.ass.models import Character
from app.db.session import SessionFactory
from app.insight.character_memory import discovery, service
from app.jobs import service as jobs
from tests.conftest import FakeAI, code
from tests.test_character_memory import memory_for
from tests.test_ssm import _manuscript


def person(name: str, quote: str, alias_of: str | None = None) -> dict:
    return {
        "name": name,
        "quote": quote,
        "kind": "ambiguous" if alias_of else "named_person",
        "alias_of": alias_of,
        "reason": "원문에서 인물의 행동을 확인함",
    }


class DiscoverAI(FakeAI):
    def __init__(self, *outputs: dict) -> None:
        super().__init__()
        self.discovery_outputs = list(outputs)

    def run_character_discovery(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("discovery", (payload,)))
        return self.discovery_outputs.pop(0)

    def run_character_memory(
        self, project_id: str, scenes: Any, characters: Any, previous_memory: Any = None
    ) -> dict[str, Any]:
        self.calls.append(("memory", (characters,)))
        return {"data": memory_for(project_id, scenes, characters)}


async def prepare(client: AsyncClient, email: str, content: str, empty: bool = True) -> dict:
    m = await _manuscript(client, email, content=content)
    m["project"] = m["base"].split("/manuscripts/")[0]
    if empty:
        assert (
            await client.delete(f"{m['project']}/characters/{m['char']}", headers=m["h"])
        ).status_code == 204
    return m


async def queue(client: AsyncClient, m: dict) -> UUID:
    response = await client.post(f"{m['base']}/character-memory", headers=m["h"])
    assert response.status_code == 202, response.text
    return UUID(response.json()["data"]["analysis_id"])


async def overview(client: AsyncClient, m: dict) -> dict:
    return (await client.get(f"{m['base']}/character-memory", headers=m["h"])).json()["data"]


async def test_empty_cast_automatically_registers_then_analyzes_and_reuses(
    client: AsyncClient,
) -> None:
    text = "🌙 서윤하는 등불을 들었다. 모윤재가 문을 열었다."
    m = await prepare(client, "discover-empty@example.com", text)
    assert (await overview(client, m))["can_analyze"]
    aid = await queue(client, m)
    assert await queue(client, m) == aid
    ai = DiscoverAI(
        {
            "data": {
                "people": [
                    person("서윤하", "서윤하는 등불을 들었다."),
                    person("모윤재", "모윤재가 문을 열었다."),
                ]
            }
        }
    )
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    state = await overview(client, m)
    assert not state["stale"] and not state["job_stale"]
    report = state["job"]["discovery"]
    assert report["complete"] and {c["status"] for c in report["candidates"]} == {"added"}
    names = {c["name"] for c in ai.calls[-1][1][0]}
    assert names == {"서윤하", "모윤재"}
    async with SessionFactory() as session:
        saved = await jobs.get(session, aid)
        assert saved and saved.result
        assert (
            saved.input["characters"]
            == (await service.source(session, saved.project_id, saved.target_id))["characters"]
        )
        assert saved.result["memory"]["characters"]
        assert len(await discovery.people(session, saved.project_id)) == 2
    assert await queue(client, m) == aid
    candidate = report["candidates"][0]
    assert (
        text[candidate["evidence"]["start"] : candidate["evidence"]["end"]]
        == candidate["evidence"]["quote"]
    )


async def test_existing_names_reused_and_ambiguous_alias_requires_author(
    client: AsyncClient,
) -> None:
    text = "피터는 고개를 들었다. 서윤하가 말했다. 윤하라고 불러도 돼요."
    m = await prepare(client, "discover-alias@example.com", text, empty=False)
    aid = await queue(client, m)
    output = {
        "data": {
            "people": [
                person("피터", "피터는 고개를 들었다."),
                person("서윤하", "서윤하가 말했다."),
                person("윤하", "윤하라고 불러도 돼요."),
            ]
        }
    }
    assert await service.handle(SessionFactory, aid, DiscoverAI(output)) == "completed"
    state = await overview(client, m)
    candidates = state["job"]["discovery"]["candidates"]
    assert next(c for c in candidates if c["name"] == "피터")["status"] == "existing"
    yunha = next(c for c in candidates if c["name"] == "서윤하")
    alias = next(c for c in candidates if c["name"] == "윤하")
    assert alias["status"] == "needs_review" and alias["character_id"] is None
    endpoint = f"{m['base']}/character-memory/{aid}/candidates/{alias['candidate_id']}/resolve"
    assert (
        code(
            await client.post(
                endpoint, headers=m["h"], json={"action": "link", "character_id": str(UUID(int=1))}
            )
        )
        == "CHARACTER_NOT_FOUND"
    )
    response = await client.post(
        endpoint, headers=m["h"], json={"action": "link", "character_id": yunha["character_id"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "confirmed"
    state = await overview(client, m)
    assert state["stale"]
    assert (
        next(c for c in state["job"]["discovery"]["candidates"] if c["name"] == "윤하")["status"]
        == "confirmed"
    )
    aid2 = await queue(client, m)
    assert aid2 != aid
    ai = DiscoverAI({"data": {"people": [person("윤하", "윤하라고 불러도 돼요.", "서윤하")]}})
    assert await service.handle(SessionFactory, aid2, ai) == "completed"
    assert next(c for c in ai.calls[-1][1][0] if c["name"] == "서윤하")["aliases"] == ["윤하"]
    assert (await overview(client, m))["job"]["discovery"]["candidates"][0]["status"] == "existing"
    async with SessionFactory() as session:
        assert len(await discovery.people(session, UUID(m["project"].split("/")[-1]))) == 2


async def test_bad_evidence_never_creates_a_character(client: AsyncClient) -> None:
    m = await prepare(client, "discover-invalid@example.com", "등불이 꺼졌다.")
    aid = await queue(client, m)
    ai = DiscoverAI({"data": {"people": [person("없는인물", "없는인물이 말했다.")]}})
    assert await service.handle(SessionFactory, aid, ai) == "failed"
    async with SessionFactory() as session:
        assert not await discovery.people(session, UUID(m["project"].split("/")[-1]))
    assert all(c[0] != "memory" for c in ai.calls)


async def test_discovery_resumes_completed_chunks_and_never_duplicates(client: AsyncClient) -> None:
    text = "서윤하는 걸었다." + "가" * 12000 + "모윤재가 기다렸다."
    m = await prepare(client, "discover-resume@example.com", text)
    aid = await queue(client, m)
    first = DiscoverAI(
        {"data": {"people": [person("서윤하", "서윤하는 걸었다.")]}},
        {"error": {"code": "AI_DISCOVERY_FAILED"}},
    )
    assert await service.handle(SessionFactory, aid, first) == "failed"
    state = await overview(client, m)
    assert state["job"]["discovery"]["processed_chunks"] == 1
    assert (
        await client.post(f"{m['base']}/character-memory/{aid}/retry", headers=m["h"])
    ).status_code == 202
    resumed = DiscoverAI({"data": {"people": [person("모윤재", "모윤재가 기다렸다.")]}})
    assert await service.handle(SessionFactory, aid, resumed) == "completed"
    assert resumed.calls[0][1][0]["start"] == discovery.CHUNK_CHARS - 300
    assert len(resumed.calls) == 2
    assert {c["name"] for c in resumed.calls[-1][1][0]} == {"서윤하", "모윤재"}
    assert not (await overview(client, m))["stale"]


async def test_source_edits_and_lost_attempts_prevent_registration(client: AsyncClient) -> None:
    m = await prepare(client, "discover-source@example.com", "서윤하가 말했다.")
    aid = await queue(client, m)
    await client.patch(m["base"], headers=m["h"], json={"content": "인물이 사라졌다."})
    assert (
        await service.handle(
            SessionFactory,
            aid,
            DiscoverAI({"data": {"people": [person("서윤하", "서윤하가 말했다.")]}}),
        )
        == "failed"
    )
    async with SessionFactory() as session:
        job = await jobs.get(session, aid)
        assert job and job.error and job.error["code"] == "DISCOVERY_SOURCE_CHANGED"
        assert not await discovery.people(session, job.project_id)
        report = deepcopy(job.result["discovery"])
        args = job.input
    with pytest.raises(discovery.DiscoveryFailure):
        await discovery.discover(SessionFactory, aid, 1, DiscoverAI(), args, report)
    async with SessionFactory() as session:
        assert not await discovery.people(session, UUID(m["project"].split("/")[-1]))


async def test_no_named_people_completes_without_fabricated_memory(client: AsyncClient) -> None:
    m = await prepare(client, "discover-none@example.com", "바람이 등불을 흔들었다.")
    aid = await queue(client, m)
    ai = DiscoverAI({"data": {"people": []}})
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    state = await overview(client, m)
    assert state["available_analysis_id"] is None
    assert state["job"]["progress"]["stage"] == "no_characters"
    assert len(ai.calls) == 1


async def test_candidate_create_dismiss_and_scope(client: AsyncClient) -> None:
    m = await prepare(client, "discover-review@example.com", "경비장이 왔다. 집사가 따랐다.")
    aid = await queue(client, m)
    candidates = [
        person("경비장", "경비장이 왔다.", "미상"),
        person("집사", "집사가 따랐다.", "미상"),
    ]
    assert (
        await service.handle(SessionFactory, aid, DiscoverAI({"data": {"people": candidates}}))
        == "completed"
    )
    state = await overview(client, m)
    rows = state["job"]["discovery"]["candidates"]
    base = f"{m['base']}/character-memory/{aid}/candidates"
    invalid = await client.post(
        f"{base}/{rows[0]['candidate_id']}/resolve",
        headers=m["h"],
        json={"action": "create", "name": " "},
    )
    assert code(invalid) == "INVALID_INPUT"
    created = await client.post(
        f"{base}/{rows[0]['candidate_id']}/resolve",
        headers=m["h"],
        json={"action": "create", "name": "경비대장"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["character_id"]
    async with SessionFactory() as session:
        character = await session.get(Character, UUID(created.json()["data"]["character_id"]))
        assert character and character.name == "경비대장" and character.aliases == ["경비장"]
    assert (
        await client.post(
            f"{base}/{rows[0]['candidate_id']}/resolve", headers=m["h"], json={"action": "create"}
        )
    ).json()["data"]["character_id"] == created.json()["data"]["character_id"]
    dismissed = await client.post(
        f"{base}/{rows[1]['candidate_id']}/resolve", headers=m["h"], json={"action": "dismiss"}
    )
    assert dismissed.json()["data"]["status"] == "dismissed"
    other = await prepare(client, "discover-other@example.com", "별빛이 비쳤다.")
    assert (
        await client.post(
            f"{base}/{rows[0]['candidate_id']}/resolve",
            headers=other["h"],
            json={"action": "create"},
        )
    ).status_code == 403
    # Same project, different manuscript cannot use another manuscript's evidence.
    mid = (
        await client.post(
            f"{m['project']}/manuscripts", headers=m["h"], json={"title": "다른 원고"}
        )
    ).json()["data"]["manuscript_id"]
    wrong = (
        f"{m['project']}/manuscripts/{mid}/character-memory/{aid}"
        f"/candidates/{rows[0]['candidate_id']}/resolve"
    )
    assert (
        code(await client.post(wrong, headers=m["h"], json={"action": "create"}))
        == "CHARACTER_MEMORY_NOT_FOUND"
    )


async def test_parallel_manuscripts_reuse_one_character(client: AsyncClient) -> None:
    m = await prepare(client, "discover-race@example.com", "서윤하가 말했다.")
    mid = (
        await client.post(
            f"{m['project']}/manuscripts", headers=m["h"], json={"title": "두 번째 원고"}
        )
    ).json()["data"]["manuscript_id"]
    other = {**m, "base": f"{m['project']}/manuscripts/{mid}"}
    await client.patch(other["base"], headers=m["h"], json={"content": "서윤하가 말했다."})
    ids = [await queue(client, x) for x in [m, other]]
    output = {"data": {"people": [person("서윤하", "서윤하가 말했다.")]}}
    results = await asyncio.gather(
        *(service.handle(SessionFactory, aid, DiscoverAI(deepcopy(output))) for aid in ids)
    )
    assert results == ["completed", "completed"]
    async with SessionFactory() as session:
        chars = await discovery.people(session, UUID(m["project"].split("/")[-1]))
        assert len(chars) == 1


def test_literal_unicode_offsets_and_missing_name_are_checked() -> None:
    payload = {"scene_id": "scene", "revision": "rev", "start": 50, "text": "🌙 서윤하가 말했다."}
    row = verified(payload, {"people": [person("서윤하", "서윤하가 말했다.")]})[0]
    assert row["evidence"]["start"] == 52
    with pytest.raises(ValueError):
        verified(payload, {"people": [person("모윤재", "서윤하가 말했다.")]})
    text = {**payload, "text": "김서진이었다. 서진은 걸었다."}
    short_only = verified(text, {"people": [person("서진", "서진은 걸었다.")]})
    assert short_only[0]["kind"] == "ambiguous"
    complete = verified(text, {"people": [person("김서진", "김서진이었다.")]})
    assert complete[0]["kind"] == "named_person"


def test_model_reviews_canonical_names_before_registering(monkeypatch: pytest.MonkeyPatch) -> None:
    from prolog_ai.core import llm

    from app.ai.character_discovery import run

    monkeypatch.delenv("USE_FAKE_LLM", raising=False)
    payload = {
        "scene_id": "s",
        "revision": "r",
        "start": 0,
        "text": "김서진이었다. 서진은 걸었다.",
        "known_people": [],
    }
    drafts = [
        {"people": [person("서진", "서진은 걸었다.")]},
        {"people": [person("김서진", "김서진이었다."), person("서진", "서진은 걸었다.", "김서진")]},
    ]
    prompts = []

    def call(prompt: str, **kwargs: Any) -> dict:
        prompts.append(prompt)
        return drafts.pop(0)

    monkeypatch.setattr(llm, "call_llm", call)
    result = run(payload)
    assert [p["name"] for p in result["data"]["people"]] == ["김서진", "서진"]
    assert len(prompts) == 2 and "초안을 원문과 다시 대조" in prompts[1]
    assert '"source_spelling": "김서진"' in prompts[1]
    assert result["meta"]["review_passes"] == 2
