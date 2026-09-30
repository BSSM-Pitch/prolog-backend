"""NLCD — 자연어 추출 잡(ai 큐) · 폴링 · 재시도 · 이력 · forward → ASS 초안.

AI 는 `FakeAI` 로 시나리오를 준다. 핸들러는 워커가 부르는 것과 같은 `job_handler.handle` 이다.
"""

from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authoring.nlcd.job_handler import handle
from app.db.session import SessionFactory
from app.events.models import OutboxEvent
from app.events.relay import message
from tests.conftest import FakeAI, code, signup
from worker.ai_worker import route

TEXT = "윤서는 신중하지만 끝까지 물고 늘어졌다. 재현이 떠난 뒤로 불안했다."
EXTRACTED = {
    "data": {
        "personality_tags": [
            {"value": "신중함", "evidence": "신중하지만"},
            {"value": "집요함", "evidence": "끝까지 물고 늘어졌다"},
        ],
        "core_values": [],
        "influence_relations": [{"value": "재현", "type": "영향", "evidence": "재현이 떠난 뒤로"}],
        "emotion_keywords": [{"value": "불안", "evidence": "불안했다"}],
    },
    "meta": {"removed_evidence_count": 1},
}


def _error(code_: str) -> dict[str, Any]:
    return {"error": {"code": code_, "message": "실패", "details": {"why": code_}}}


async def _project(client: AsyncClient, email: str) -> tuple[dict, str]:
    user = await signup(client, email)
    res = await client.post(
        "/projects", json={"title": "P", "owner_type": "personal"}, headers=user["headers"]
    )
    return user, res.json()["data"]["project_id"]


async def _submit(client: AsyncClient, user: dict, pid: str, **body: Any) -> dict:
    res = await client.post(
        f"/projects/{pid}/nl-extractions",
        json={"source_text": TEXT} | body,
        headers=user["headers"],
    )
    assert res.status_code == 202, res.text
    return res.json()


async def _get(client: AsyncClient, user: dict, pid: str, eid: str) -> dict:
    res = await client.get(f"/projects/{pid}/nl-extractions/{eid}", headers=user["headers"])
    assert res.status_code == 200, res.text
    return res.json()


async def test_submit_extract_and_poll(client: AsyncClient, db: AsyncSession) -> None:
    user, pid = await _project(client, "nl1@example.com")
    submitted = await _submit(client, user, pid)
    eid = submitted["data"]["extraction_id"]
    assert submitted["data"]["status"] == "analyzing"
    assert submitted["data"]["personality_tags"] == [] and submitted["meta"] == {}

    # 잡은 ai 큐로 알려지고, ai 워커가 받는 메시지다
    event = (
        await db.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(eid)))
    ).scalar_one()
    assert event.payload["queue"] == "ai"
    routed = route(message(event))
    assert routed is not None and routed[1] == UUID(eid)

    ai = FakeAI(EXTRACTED)
    assert await handle(SessionFactory, UUID(eid), ai) == "completed"
    assert ai.calls == [("run_nlcd", (TEXT,))]
    got = await _get(client, user, pid, eid)
    assert got["data"]["status"] == "completed"
    assert got["data"]["personality_tags"] == EXTRACTED["data"]["personality_tags"]
    assert got["data"]["influence_relations"][0]["type"] == "영향"
    assert got["meta"] == {"removed_evidence_count": 1}
    assert got["error"] is None  # 실패일 때만 채워진다

    # 같은 잡이 다시 와도(중복 수신) AI 를 다시 부르지 않는다
    again = FakeAI()
    assert await handle(SessionFactory, UUID(eid), again) == "skipped"
    assert again.calls == []


async def test_duplicate_sentence_is_flagged_not_blocked(client: AsyncClient) -> None:
    user, pid = await _project(client, "nl2@example.com")
    first = (await _submit(client, user, pid))["data"]
    second = await _submit(client, user, pid, source_text="  " + TEXT.replace(" ", "  ") + " ")
    assert second["data"]["duplicate_of"] == first["extraction_id"]
    assert "notice" in second["meta"]
    other = await _submit(client, user, pid, source_text="전혀 다른 문장")
    assert other["data"]["duplicate_of"] is None


async def test_failure_is_200_with_error_and_user_retry(client: AsyncClient) -> None:
    user, pid = await _project(client, "nl3@example.com")
    eid = (await _submit(client, user, pid))["data"]["extraction_id"]

    # AI_*_FAILED 는 자동 재시도하지 않는다(패키지가 이미 재시도한 뒤다)
    assert (
        await handle(SessionFactory, UUID(eid), FakeAI(_error("AI_EXTRACTION_FAILED"))) == "failed"
    )
    got = await _get(client, user, pid, eid)
    assert got["data"]["status"] == "failed"
    assert got["error"] == {
        "code": "AI_EXTRACTION_FAILED",
        "message": "실패",
        "details": {"why": "AI_EXTRACTION_FAILED"},
    }

    res = await client.post(f"/projects/{pid}/nl-extractions/{eid}/retry", headers=user["headers"])
    assert res.status_code == 202 and res.json()["data"]["status"] == "analyzing"
    assert await handle(SessionFactory, UUID(eid), FakeAI(EXTRACTED)) == "completed"
    res = await client.post(f"/projects/{pid}/nl-extractions/{eid}/retry", headers=user["headers"])
    assert code(res) == "EXTRACTION_NOT_READY"
    assert res.json()["error"]["details"] == {"status": "completed"}


async def test_timeout_retries_automatically_until_exhausted(client: AsyncClient) -> None:
    user, pid = await _project(client, "nl4@example.com")
    eid = UUID((await _submit(client, user, pid))["data"]["extraction_id"])
    timeout = _error("AI_EXTRACTION_TIMEOUT")
    outcomes = [await handle(SessionFactory, eid, FakeAI(timeout)) for _ in range(3)]
    assert outcomes == ["retried", "retried", "exhausted"]
    assert (await _get(client, user, pid, str(eid)))["error"]["code"] == "AI_EXTRACTION_TIMEOUT"

    # 자동 재시도를 다 써도 사용자는 되살린다
    res = await client.post(f"/projects/{pid}/nl-extractions/{eid}/retry", headers=user["headers"])
    assert res.status_code == 202
    assert await handle(SessionFactory, eid, FakeAI(EXTRACTED)) == "completed"


async def test_invalid_input_from_package_is_not_retried(client: AsyncClient) -> None:
    user, pid = await _project(client, "nl5@example.com")
    eid = UUID((await _submit(client, user, pid))["data"]["extraction_id"])
    assert await handle(SessionFactory, eid, FakeAI(_error("INVALID_INPUT"))) == "failed"


async def test_blank_text_is_rejected_before_a_job(client: AsyncClient) -> None:
    user, pid = await _project(client, "nl6@example.com")
    res = await client.post(
        f"/projects/{pid}/nl-extractions", json={"source_text": "   "}, headers=user["headers"]
    )
    assert code(res) == "INVALID_INPUT"


async def test_forward_creates_an_ai_draft_once(client: AsyncClient) -> None:
    user, pid = await _project(client, "nl7@example.com")
    h = user["headers"]
    eid = (await _submit(client, user, pid))["data"]["extraction_id"]
    fwd = f"/projects/{pid}/nl-extractions/{eid}/forward"

    res = await client.post(fwd, json={}, headers=h)
    assert code(res) == "EXTRACTION_NOT_READY"
    assert res.json()["error"]["details"] == {"status": "analyzing"}

    await handle(SessionFactory, UUID(eid), FakeAI(EXTRACTED))
    res = await client.post(fwd, json={}, headers=h)
    assert res.status_code == 201, res.text
    draft_id = res.json()["data"]["forwarded_draft_id"]

    again = await client.post(fwd, json={}, headers=h)
    assert code(again) == "ALREADY_FORWARDED"
    assert again.json()["error"]["details"] == {"forwarded_draft_id": draft_id}
    assert (await _get(client, user, pid, eid))["data"]["forwarded_draft_id"] == draft_id

    draft = (await client.get(f"/projects/{pid}/character-drafts/{draft_id}", headers=h)).json()[
        "data"
    ]
    assert draft["source_job_id"] == eid
    assert draft["character_name"] is None  # 추출은 이름을 뽑지 않는다
    assert [(i["value"], i["evidence"], i["origin"]) for i in draft["personality_tags"]] == [
        ("신중함", "신중하지만", "ai_extracted"),
        ("집요함", "끝까지 물고 늘어졌다", "ai_extracted"),
    ]
    assert [i["value"] for i in draft["influence_relations"]] == ["재현"]
    # AI 가 넣은 항목은 사용자 편집 이력이 아니다
    history = await client.get(
        f"/projects/{pid}/character-drafts/{draft_id}/edit-history", headers=h
    )
    assert history.json()["data"] == []


async def test_target_character(client: AsyncClient) -> None:
    user, pid = await _project(client, "nl8@example.com")
    h = user["headers"]
    res = await client.post(
        f"/projects/{pid}/nl-extractions",
        json={"source_text": TEXT, "target_character_id": str(UUID(int=1))},
        headers=h,
    )
    assert code(res) == "TARGET_CHARACTER_NOT_FOUND"

    did = (
        await client.post(
            f"/projects/{pid}/character-drafts", json={"character_name": "윤서"}, headers=h
        )
    ).json()["data"]["draft_id"]
    char = (
        await client.post(f"/projects/{pid}/character-drafts/{did}/confirm", json={}, headers=h)
    ).json()["data"]["character_id"]
    eid = (await _submit(client, user, pid, target_character_id=char))["data"]["extraction_id"]
    await handle(SessionFactory, UUID(eid), FakeAI(EXTRACTED))

    listed = await client.get(
        f"/projects/{pid}/nl-extractions?target_character_id={char}", headers=h
    )
    assert [e["extraction_id"] for e in listed.json()["data"]] == [eid]
    draft_id = (
        await client.post(f"/projects/{pid}/nl-extractions/{eid}/forward", json={}, headers=h)
    ).json()["data"]["forwarded_draft_id"]
    # 이름이 대상 캐릭터로 채워져, 확정하면 병합 후보로 이어진다
    res = await client.post(
        f"/projects/{pid}/character-drafts/{draft_id}/confirm", json={}, headers=h
    )
    assert res.json()["error"]["details"] == {"candidate_character_id": char}


async def test_history_filters_and_cursor(client: AsyncClient) -> None:
    user, pid = await _project(client, "nl9@example.com")
    h = user["headers"]
    ids = [
        (await _submit(client, user, pid, source_text=f"문장 {n}"))["data"]["extraction_id"]
        for n in range(3)
    ]
    await handle(SessionFactory, UUID(ids[0]), FakeAI(EXTRACTED))
    base = f"/projects/{pid}/nl-extractions"
    analyzing = (await client.get(f"{base}?status=analyzing", headers=h)).json()["data"]
    assert [e["extraction_id"] for e in analyzing] == [ids[2], ids[1]]
    assert "personality_tags" not in analyzing[0]  # 요약만
    page = (await client.get(f"{base}?limit=2", headers=h)).json()
    rest = (
        await client.get(f"{base}?limit=2&cursor={page['meta']['next_cursor']}", headers=h)
    ).json()
    assert [e["extraction_id"] for e in page["data"] + rest["data"]] == ids[::-1]
    assert code(await client.get(f"{base}/{UUID(int=2)}", headers=h)) == "EXTRACTION_NOT_FOUND"


async def test_name_from_screen_22_becomes_the_draft_name(client: AsyncClient) -> None:
    user, pid = await _project(client, "nl10@example.com")
    h = user["headers"]
    submitted = await _submit(client, user, pid, name=" 윤서 ")
    eid = submitted["data"]["extraction_id"]
    assert submitted["data"]["name"] == "윤서"
    await handle(SessionFactory, UUID(eid), FakeAI(EXTRACTED))
    draft_id = (
        await client.post(f"/projects/{pid}/nl-extractions/{eid}/forward", json={}, headers=h)
    ).json()["data"]["forwarded_draft_id"]
    draft = (await client.get(f"/projects/{pid}/character-drafts/{draft_id}", headers=h)).json()
    assert draft["data"]["character_name"] == "윤서"
