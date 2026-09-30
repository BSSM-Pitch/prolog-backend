"""REX AI 추출 — 원고 → 후보(ai 잡) → 사용자가 골라 확정(origin = ai_extracted)."""

from typing import Any
from uuid import UUID

from httpx import AsyncClient

from app.authoring.rex.extraction import handle
from app.db.session import SessionFactory
from tests.conftest import FakeAI, code, signup

BODY = "비가 멎자 문의 윤곽이 붉게 떠올랐다. 기록 열람에는 위원 두 명의 승인이 필요했다."
RULES = {
    "data": {
        "extracted_rules": [
            {
                "title": "붉은 문",
                "description": "붉은 문은 비가 그친 뒤에만 열린다",
                "violation_keywords": ["비 오는 중에 열림"],
                "evidence": "비가 멎자 문의 윤곽이 붉게 떠올랐다.",
                "source_chapter": None,
            },
            {
                "title": "기록 열람",
                "description": "기록 열람에는 위원 2인의 승인이 필요하다",
                "violation_keywords": ["혼자 열람"],
                "evidence": "기록 열람에는 위원 두 명의 승인이 필요했다.",
                "source_chapter": None,
            },
        ]
    },
    "meta": {"removed_evidence_count": 0},
}


async def _manuscript(
    client: AsyncClient, email: str, content: str | None = BODY
) -> dict[str, Any]:
    user = await signup(client, email)
    h = user["headers"]
    pid = (
        await client.post("/projects", json={"title": "P", "owner_type": "personal"}, headers=h)
    ).json()["data"]["project_id"]
    mid = (
        await client.post(f"/projects/{pid}/manuscripts", json={"title": "원고"}, headers=h)
    ).json()["data"]["manuscript_id"]
    if content:
        await client.patch(
            f"/projects/{pid}/manuscripts/{mid}", json={"content": content}, headers=h
        )
    base = f"/projects/{pid}/manuscripts/{mid}/rule-extractions"
    return {"h": h, "pid": pid, "mid": mid, "base": base}


async def _extract(client: AsyncClient, m: dict[str, Any], *results: dict[str, Any]) -> str:
    res = await client.post(m["base"], headers=m["h"])
    assert res.status_code == 202, res.text
    assert res.json()["data"]["status"] == "queued"
    eid = res.json()["data"]["extraction_id"]
    ai = FakeAI(*results)
    await handle(SessionFactory, UUID(eid), ai)
    assert ai.calls == [("run_rex", (BODY,))] or not results
    return str(eid)


async def test_extract_review_and_confirm(client: AsyncClient) -> None:
    m = await _manuscript(client, "rx1@example.com")
    h, base = m["h"], m["base"]
    eid = await _extract(client, m, RULES)
    got = (await client.get(f"{base}/{eid}", headers=h)).json()
    assert got["data"]["status"] == "completed" and got["error"] is None
    assert [
        (c["index"], c["title"], c["review_status"]) for c in got["data"]["extracted_rules"]
    ] == [
        (0, "붉은 문", "pending"),
        (1, "기록 열람", "pending"),
    ]

    body = {
        "selected_indices": [0],
        "edits": {"0": {"violation_keywords": ["비 오는 중에 열림", "맑은 날 개방"]}},
        "ignored_indices": [1],
    }
    res = await client.post(f"{base}/{eid}/confirm", json=body, headers=h)
    assert res.status_code == 201, res.text
    [rule] = res.json()["data"]
    assert (rule["origin"], rule["extraction_id"], rule["title"]) == (
        "ai_extracted",
        eid,
        "붉은 문",
    )
    assert rule["violation_keywords"] == ["비 오는 중에 열림", "맑은 날 개방"]
    assert rule["evidence"] == "비가 멎자 문의 윤곽이 붉게 떠올랐다."

    reviewed = (await client.get(f"{base}/{eid}", headers=h)).json()["data"]["extracted_rules"]
    assert [(c["review_status"], c["rule_id"]) for c in reviewed] == [
        ("confirmed", rule["rule_id"]),
        ("ignored", None),
    ]
    # 원본 후보는 그대로다 — 고친 값은 규칙에만
    assert reviewed[0]["violation_keywords"] == ["비 오는 중에 열림"]

    # 두 번 눌러도 규칙은 하나. 무시한 후보는 나중에 확정할 수 있다
    again = await client.post(f"{base}/{eid}/confirm", json={"selected_indices": [0, 1]}, headers=h)
    assert [r["title"] for r in again.json()["data"]] == ["기록 열람"]
    listed = (await client.get(f"/projects/{m['pid']}/world-rules", headers=h)).json()["data"]
    assert sorted(r["title"] for r in listed) == ["기록 열람", "붉은 문"]

    bad = await client.post(f"{base}/{eid}/confirm", json={"selected_indices": [7]}, headers=h)
    assert code(bad) == "INVALID_INPUT"
    both = {"selected_indices": [0], "ignored_indices": [0]}
    assert code(await client.post(f"{base}/{eid}/confirm", json=both, headers=h)) == "INVALID_INPUT"


async def test_not_ready_failure_and_retry(client: AsyncClient) -> None:
    m = await _manuscript(client, "rx2@example.com")
    h, base = m["h"], m["base"]
    eid = (await client.post(base, headers=h)).json()["data"]["extraction_id"]
    res = await client.post(f"{base}/{eid}/confirm", json={"selected_indices": [0]}, headers=h)
    assert code(res) == "EXTRACTION_NOT_READY"

    failed = {"error": {"code": "AI_EXTRACTION_FAILED", "message": "실패", "details": {}}}
    assert await handle(SessionFactory, UUID(eid), FakeAI(failed)) == "failed"
    got = (await client.get(f"{base}/{eid}", headers=h)).json()
    assert (got["data"]["status"], got["error"]["code"]) == ("failed", "AI_EXTRACTION_FAILED")
    res = await client.post(f"{base}/{eid}/retry", headers=h)
    assert res.status_code == 202 and res.json()["data"]["status"] == "queued"
    assert await handle(SessionFactory, UUID(eid), FakeAI(RULES)) == "completed"
    assert code(await client.post(f"{base}/{eid}/retry", headers=h)) == "EXTRACTION_NOT_READY"


async def test_manuscript_must_have_body(client: AsyncClient) -> None:
    m = await _manuscript(client, "rx3@example.com", content=None)
    res = await client.post(m["base"], headers=m["h"])
    assert code(res) == "INVALID_INPUT"


async def test_manuscript_deleted_before_run_fails_without_retry(client: AsyncClient) -> None:
    m = await _manuscript(client, "rx4@example.com")
    h = m["h"]
    eid = (await client.post(m["base"], headers=h)).json()["data"]["extraction_id"]
    await client.delete(f"/projects/{m['pid']}/manuscripts/{m['mid']}", headers=h)
    ai = FakeAI(RULES)
    assert await handle(SessionFactory, UUID(eid), ai) == "failed"
    assert ai.calls == []  # AI 를 부르지 않았다


async def test_extraction_belongs_to_its_manuscript(client: AsyncClient) -> None:
    m = await _manuscript(client, "rx5@example.com")
    eid = (await client.post(m["base"], headers=m["h"])).json()["data"]["extraction_id"]
    other = (
        await client.post(
            f"/projects/{m['pid']}/manuscripts", json={"title": "다른 원고"}, headers=m["h"]
        )
    ).json()["data"]["manuscript_id"]
    wrong = m["base"].replace(m["mid"], other)
    assert code(await client.get(f"{wrong}/{eid}", headers=m["h"])) == "RULE_EXTRACTION_NOT_FOUND"
