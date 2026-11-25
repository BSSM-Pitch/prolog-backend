import asyncio
from copy import deepcopy
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.ai.relationship_draft import validate
from app.db.session import SessionFactory
from app.insight.rcv import draft
from app.jobs import service as jobs
from tests.conftest import FakeAI
from tests.test_relationships import setup


def result(people: list[str]) -> dict:
    return {
        "source_character_id": people[0],
        "target_character_id": people[1],
        "state": "신뢰",
        "trust": None,
        "event_title": "함께 원문을 확인했다",
        "explanation": "작가가 확인할 관계 초안",
        "evidence": ["1장의 원문"],
    }


async def test_cached_durable_draft_and_writer_confirmation(client: AsyncClient) -> None:
    s = await setup(client)
    url = s["base"] + "/relationship-drafts"
    body = {"manuscript_id": s["ms"][0], "chapter_id": s["chapters"][0][1]}
    responses = await asyncio.gather(
        *[client.post(url, headers=s["h"], json=body) for _ in range(2)]
    )
    assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
    first, second = [r.json()["data"] for r in responses]
    assert first["draft_id"] == second["draft_id"]
    jid = UUID(first["draft_id"])
    ai = FakeAI({"data": result(s["people"])})
    assert await draft.handle(SessionFactory, jid, ai) == "completed"
    assert await draft.handle(SessionFactory, jid, ai) == "skipped"
    final = (await client.get(url + f"/{jid}", headers=s["h"])).json()["data"]
    assert final["result"]["trust"] is None
    assert not final["stale"]
    again = (await client.post(url, headers=s["h"], json=body)).json()["data"]
    assert again["draft_id"] == str(jid) and again["status"] == "completed"
    # AI drafts never create the actual relationship or consume another model run on reopen.
    assert (await client.get(s["base"] + "/relationships", headers=s["h"])).json()["data"] == []
    async with SessionFactory() as session:
        job = await jobs.get(session, jid)
        assert job and job.max_attempt == 1
    changed = await client.patch(
        s["base"] + f"/chapters/{s['chapters'][0][1]}",
        headers=s["h"],
        json={"content": "수정된 원문"},
    )
    assert changed.status_code == 200, changed.text
    stale = (await client.get(url + f"/{jid}", headers=s["h"])).json()["data"]
    assert stale["stale"]
    fresh = (await client.post(url, headers=s["h"], json=body)).json()["data"]
    assert fresh["draft_id"] != str(jid)


async def test_scope_permissions_bounds_and_explicit_retry(client: AsyncClient) -> None:
    s = await setup(client)
    other = await setup(client, "other-draft@example.com")
    url = s["base"] + "/relationship-drafts"
    body = {"manuscript_id": s["ms"][0], "chapter_id": s["chapters"][0][1]}
    denied = await client.post(url, headers=other["h"], json=body)
    assert denied.status_code == 403
    wrong = await client.post(url, headers=s["h"], json=body | {"chapter_id": s["chapters"][1][1]})
    assert wrong.status_code == 404
    wrong_pair = await client.post(
        url, headers=s["h"], json=body | {"selected_pair": other["people"][:2]}
    )
    assert wrong_pair.status_code == 404
    await client.patch(
        s["base"] + f"/chapters/{s['chapters'][0][1]}",
        headers=s["h"],
        json={"content": "가" * 15000},
    )
    job = (await client.post(url, headers=s["h"], json=body)).json()["data"]
    assert job["analyzed_chars"] == 12000 and job["total_chars"] == 15000
    jid = UUID(job["draft_id"])
    assert (
        await draft.handle(SessionFactory, jid, FakeAI({"data": result(s["people"])})) == "failed"
    )
    failed = (await client.post(url, headers=s["h"], json=body)).json()["data"]
    assert failed["status"] == "failed" and failed["draft_id"] == str(jid)
    retried = (await client.post(url, headers=s["h"], json=body | {"retry": True})).json()["data"]
    assert retried["status"] == "queued" and retried["draft_id"] == str(jid)
    assert (await client.get(url + f"/{jid}", headers=other["h"])).status_code == 403


def test_rejects_unknown_characters_wrong_pair_and_invented_quotes() -> None:
    payload = {
        "characters": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        "text": "1장의 원문",
        "selected_pair": ["a", "b"],
    }
    valid = result(["a", "b"])
    assert validate(valid, payload)["state"] == "신뢰"
    for updates in [
        {"source_character_id": "unknown"},
        {"target_character_id": "a"},
        {"target_character_id": "c"},
        {"evidence": ["없는 인용"]},
        {"trust": 101},
    ]:
        with pytest.raises(ValueError):
            validate(deepcopy(valid) | updates, payload)
    insufficient = {
        "source_character_id": None,
        "target_character_id": None,
        "state": "",
        "trust": None,
        "event_title": "",
        "explanation": "근거 부족",
        "evidence": [],
    }
    assert validate(insufficient, payload)["source_character_id"] is None


def test_model_adapter_validates_quotes_and_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai.relationship_draft import RelationshipSuggestion, run

    payload = {"text": "1장의 원문", "characters": [{"id": "a"}, {"id": "b"}]}
    calls = []

    def llm(prompt, **options):
        calls.append(options)
        return result(["a", "b"])

    monkeypatch.setattr("prolog_ai.core.llm.call_llm", llm)
    assert run(payload)["data"]["state"] == "신뢰"
    assert calls == [{"schema": RelationshipSuggestion, "timeout": 90, "max_retries": 0}]
    assert "error" in run({**payload, "text": "다른 원문"})
