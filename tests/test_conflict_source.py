"""An edit must change exactly its reviewed passage, or leave everything pending."""

from uuid import UUID

import pytest
from httpx import AsyncClient

from app.db.session import SessionFactory
from app.insight.scds.service import handle
from app.insight.scds.source import edit_problem
from tests.conftest import FakeAI
from tests.test_scds import _event, _world, advice_for

QUOTE = "정오에 격벽이 열렸다. 산소 공급이 멈췄다."
TEXT = "하린은 녹슨 손잡이를 닦았다.\n\n" + QUOTE + "\n\n창밖에 먼지가 내려앉았다."


async def reviewed(client: AsyncClient, text: str = TEXT) -> tuple[dict, dict]:
    w = await _world(client, "writer-edit@example.com")
    path = f"/projects/{w['pid']}/chapters/{w['chapter']}"
    assert (await client.patch(path, headers=w["h"], json={"content": text})).status_code == 200
    check = (await _event(client, w, text))["conflict_check"]
    result = advice_for(w)
    result["data"]["assessments"][0]["evidence"] = QUOTE
    assert await handle(SessionFactory, UUID(check["check_id"]), FakeAI(result)) == "completed"
    rows = (await client.get(f"/projects/{w['pid']}/conflicts", headers=w["h"])).json()["data"]
    return w, rows[0]


async def test_multisentence_edit_and_history_are_saved_together(client: AsyncClient) -> None:
    w, c = await reviewed(client)
    assert c["matched_keyword"] == QUOTE and c["edit_unavailable_reason"] is None
    fixed = "정오에도 격벽은 닫혀 있었다. 산소 공급은 계속됐다."
    res = await client.patch(
        f"/projects/{w['pid']}/conflicts/{c['conflict_id']}",
        headers=w["h"],
        json={"action": "modified", "modified_content": fixed, "source_hash": c["source_hash"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["status"] == "modified"
    chapter = (
        await client.get(f"/projects/{w['pid']}/chapters/{w['chapter']}", headers=w["h"])
    ).json()["data"]
    assert chapter["content"] == TEXT.replace(QUOTE, fixed)
    base = f"/projects/{w['pid']}/manuscripts/{chapter['manuscript_id']}"
    manuscript = (await client.get(base, headers=w["h"])).json()["data"]
    assert fixed in manuscript["content"] and QUOTE not in manuscript["content"]
    versions = (await client.get(f"{base}/versions", headers=w["h"])).json()["data"]
    assert len(versions) == 2
    assert fixed in versions[0]["content"] and QUOTE in versions[1]["content"]
    restored = await client.post(
        f"{base}/versions/{versions[1]['version_id']}/restore", headers=w["h"]
    )
    assert restored.status_code == 201, restored.text
    copy = restored.json()["data"]
    assert copy["manuscript_id"] != chapter["manuscript_id"] and QUOTE in copy["content"]
    unchanged = (await client.get(base, headers=w["h"])).json()["data"]
    assert unchanged["content"] == manuscript["content"]


@pytest.mark.parametrize("kind", ["changed", "duplicate", "missing_hash"])
async def test_unsafe_edits_do_not_resolve_or_modify(client: AsyncClient, kind: str) -> None:
    original = TEXT + "\n" + QUOTE if kind == "duplicate" else TEXT
    w, c = await reviewed(client, original)
    if kind == "changed":
        original += "\n하린은 일지를 덮었다."
        await client.patch(
            f"/projects/{w['pid']}/chapters/{w['chapter']}",
            headers=w["h"],
            json={"content": original},
        )
    body = {"action": "modified", "modified_content": "바꾸면 안 되는 내용"}
    if kind != "missing_hash":
        body["source_hash"] = c["source_hash"]
    path = f"/projects/{w['pid']}/conflicts/{c['conflict_id']}"
    res = await client.patch(path, headers=w["h"], json=body)
    assert res.status_code == 400, res.text
    assert (await client.get(path, headers=w["h"])).json()["data"]["status"] == "pending"
    got = await client.get(f"/projects/{w['pid']}/chapters/{w['chapter']}", headers=w["h"])
    assert got.json()["data"]["content"] == original


def test_quotation_is_never_guessed_from_first_sentence() -> None:
    assert edit_problem(TEXT, TEXT, "없는 문장")
    assert edit_problem(TEXT, TEXT, QUOTE) is None
    assert edit_problem(TEXT, TEXT.replace("\n\n", "\n"), QUOTE) is None
    assert edit_problem(TEXT, TEXT.replace("격벽", "방벽"), QUOTE)
