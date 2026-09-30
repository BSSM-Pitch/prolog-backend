"""ASS — 캐릭터 수동 경로(origin = user_added).

확정은 동기다. evidence·origin 은 draft_items → character_attributes 로 그대로 승계된다.
편집 이력은 append-only 이고, 화면의 "편집 이력 N건" 은 항목 하나의 이력이다.
"""

import asyncio
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authoring.ass.models import CharacterDraftItem
from app.main import app
from tests.conftest import code, signup


async def _project(client: AsyncClient, email: str) -> tuple[dict, str]:
    user = await signup(client, email)
    res = await client.post(
        "/projects", json={"title": "P", "owner_type": "personal"}, headers=user["headers"]
    )
    return user, res.json()["data"]["project_id"]


async def _draft(client: AsyncClient, user: dict, pid: str, name: str | None = "윤서") -> str:
    res = await client.post(
        f"/projects/{pid}/character-drafts", json={"character_name": name}, headers=user["headers"]
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]["draft_id"]


async def _item(
    client: AsyncClient, user: dict, pid: str, did: str, field: str, value: str
) -> dict:
    res = await client.post(
        f"/projects/{pid}/character-drafts/{did}/items",
        json={"field": field, "value": value},
        headers=user["headers"],
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def test_manual_draft_edit_and_confirm(client: AsyncClient, db: AsyncSession) -> None:
    user, pid = await _project(client, "ch1@example.com")
    h = user["headers"]
    did = await _draft(client, user, pid, name=None)
    base = f"/projects/{pid}/character-drafts/{did}"

    empty = (await client.get(base, headers=h)).json()["data"]
    assert empty["status"] == "pending_review"
    assert empty["source_job_id"] is None
    assert empty["character_name"] is None
    assert all(empty[f] == [] for f in ("personality_tags", "core_values", "influence_relations"))

    tag = await _item(client, user, pid, did, "personality_tags", "신중함")
    assert tag["origin"] == "user_added" and tag["evidence"] is None
    await _item(client, user, pid, did, "core_values", "약속 중시")
    gone = await _item(client, user, pid, did, "emotion_keywords", "분노")

    # AI 가 뽑은 항목 하나를 직접 넣는다 — 승계 검사용(이번 범위에 NLCD 경로는 없다).
    ai = CharacterDraftItem(
        project_id=UUID(pid),
        draft_id=UUID(did),
        category="influence_relation",
        value="재현",
        evidence="재현이 떠난 뒤로 윤서는",
        origin="ai_extracted",
    )
    db.add(ai)
    await db.commit()

    # 값 수정 · 카테고리 수정 · 삭제 · 이름
    res = await client.patch(f"{base}/items/{tag['item_id']}", json={"value": "집요함"}, headers=h)
    assert res.json()["data"]["value"] == "집요함"
    res = await client.patch(
        f"{base}/items/{tag['item_id']}", json={"field": "emotion_keywords"}, headers=h
    )
    assert res.json()["data"]["field"] == "emotion_keywords"
    res = await client.patch(f"{base}/items/{ai.id}", json={"value": "재현 선배"}, headers=h)
    assert res.json()["data"]["evidence"] == "재현이 떠난 뒤로 윤서는"
    assert res.json()["data"]["origin"] == "ai_extracted"
    assert (await client.delete(f"{base}/items/{gone['item_id']}", headers=h)).status_code == 204
    await client.patch(base, json={"character_name": "윤서"}, headers=h)

    # 항목 하나의 이력 — 값 변경과 카테고리 변경 둘, 추가까지 셋
    one = (await client.get(f"{base}/edit-history?item_id={tag['item_id']}", headers=h)).json()
    assert [(e["action"], e["field"], e["value"]) for e in one["data"]] == [
        ("added", "personality_tags", "신중함"),
        ("modified", "personality_tags", "집요함"),
        ("modified", "category", "emotion_keywords"),
    ]
    whole = (await client.get(f"{base}/edit-history", headers=h)).json()["data"]
    assert ("removed", "emotion_keywords", "분노") in [
        (e["action"], e["field"], e["value"]) for e in whole
    ]
    assert whole[-1]["field"] == "character_name"

    res = await client.post(f"{base}/confirm", json={}, headers=h)
    assert res.status_code == 201, res.text
    char = res.json()["data"]
    assert char["name"] == "윤서" and char["status"] == "confirmed"
    assert char["created_from_draft_id"] == did
    assert [(a["value"], a["origin"]) for a in char["emotion_keywords"]] == [
        ("집요함", "user_added")
    ]
    assert [(a["value"], a["evidence"], a["origin"]) for a in char["influence_relations"]] == [
        ("재현 선배", "재현이 떠난 뒤로 윤서는", "ai_extracted")
    ]
    assert [a["value"] for a in char["core_values"]] == ["약속 중시"]

    after = (await client.get(base, headers=h)).json()["data"]
    assert after["status"] == "confirmed"
    assert after["confirmed_character_id"] == char["character_id"]
    # 확정 후에는 초안을 고칠 수 없다
    res = await client.post(f"{base}/items", json={"field": "core_values", "value": "x"}, headers=h)
    assert code(res) == "DRAFT_ALREADY_RESOLVED"
    assert (
        code(await client.post(f"{base}/confirm", json={}, headers=h)) == "DRAFT_ALREADY_RESOLVED"
    )
    # 초안 단계 이력에는 확정 이력이 섞이지 않는다
    again = (await client.get(f"{base}/edit-history", headers=h)).json()["data"]
    assert len(again) == len(whole)

    history = await client.get(
        f"/projects/{pid}/characters/{char['character_id']}/edit-history", headers=h
    )
    entries = history.json()["data"]
    assert entries[0]["field"] == "name"
    assert {e["item_id"] for e in entries[1:]} == {
        a["attribute_id"]
        for f in ("personality_tags", "core_values", "influence_relations", "emotion_keywords")
        for a in char[f]
    }


async def test_confirm_requires_name(client: AsyncClient) -> None:
    user, pid = await _project(client, "ch2@example.com")
    did = await _draft(client, user, pid, name=None)
    res = await client.post(
        f"/projects/{pid}/character-drafts/{did}/confirm", json={}, headers=user["headers"]
    )
    assert res.status_code == 400
    assert code(res) == "MISSING_REQUIRED_FIELD"
    assert res.json()["error"]["details"] == {"field": "character_name"}


async def test_duplicate_name_then_merge_or_rename(client: AsyncClient) -> None:
    user, pid = await _project(client, "ch3@example.com")
    h = user["headers"]
    first = await _draft(client, user, pid)
    await _item(client, user, pid, first, "core_values", "약속 중시")
    char = (
        await client.post(f"/projects/{pid}/character-drafts/{first}/confirm", json={}, headers=h)
    ).json()["data"]

    second = await _draft(client, user, pid, name="윤서")
    await _item(client, user, pid, second, "core_values", "약속 중시")  # 이미 있는 값
    await _item(client, user, pid, second, "personality_tags", "신중함")
    confirm = f"/projects/{pid}/character-drafts/{second}/confirm"

    res = await client.post(confirm, json={}, headers=h)
    assert res.status_code == 409
    assert code(res) == "DUPLICATE_CHARACTER_CANDIDATE"
    assert res.json()["error"]["details"] == {"candidate_character_id": char["character_id"]}
    # 409 뒤에도 초안은 그대로다(화면의 "다시 시도")
    draft = (await client.get(f"/projects/{pid}/character-drafts/{second}", headers=h)).json()
    assert draft["data"]["status"] == "pending_review"

    res = await client.post(
        confirm,
        json={"resolution": "merge", "merge_target_character_id": char["character_id"]},
        headers=h,
    )
    assert res.status_code == 200, res.text
    merged = res.json()["data"]
    assert merged["character_id"] == char["character_id"]
    assert [a["value"] for a in merged["core_values"]] == ["약속 중시"]  # 중복은 건너뛴다
    assert [a["value"] for a in merged["personality_tags"]] == ["신중함"]

    # 화면 37 "새 인물로 만들기": 같은 이름의 인물이 하나 더 생긴다
    third = await _draft(client, user, pid, name="윤서")
    res = await client.post(
        f"/projects/{pid}/character-drafts/{third}/confirm",
        json={"resolution": "create_new"},
        headers=h,
    )
    assert res.status_code == 201, res.text
    twin = res.json()["data"]
    assert twin["name"] == "윤서" and twin["character_id"] != char["character_id"]
    listed = (await client.get(f"/projects/{pid}/characters", headers=h)).json()["data"]
    assert sorted(c["name"] for c in listed) == ["윤서", "윤서"]

    # 동명이 둘이어도 후보는 가장 먼저 만든 쪽이다
    fourth = await _draft(client, user, pid, name="윤서")
    res = await client.post(
        f"/projects/{pid}/character-drafts/{fourth}/confirm", json={}, headers=h
    )
    assert res.json()["error"]["details"] == {"candidate_character_id": char["character_id"]}


async def test_same_name_confirmed_concurrently_makes_one(client: AsyncClient) -> None:
    """제약이 없으므로 앱 판정이 경합에서 뚫리면 사용자가 고르지 않은 동명이 생긴다."""
    user, pid = await _project(client, "ch8@example.com")
    drafts = [await _draft(client, user, pid, name="민아") for _ in range(2)]
    results = await asyncio.gather(
        *(
            client.post(
                f"/projects/{pid}/character-drafts/{d}/confirm", json={}, headers=user["headers"]
            )
            for d in drafts
        )
    )
    assert sorted(r.status_code for r in results) == [201, 409]


async def test_merge_target_must_exist(client: AsyncClient) -> None:
    user, pid = await _project(client, "ch4@example.com")
    did = await _draft(client, user, pid)
    res = await client.post(
        f"/projects/{pid}/character-drafts/{did}/confirm",
        json={"resolution": "merge", "merge_target_character_id": str(UUID(int=1))},
        headers=user["headers"],
    )
    assert code(res) == "CHARACTER_NOT_FOUND"


async def test_discard(client: AsyncClient) -> None:
    user, pid = await _project(client, "ch5@example.com")
    h = user["headers"]
    did = await _draft(client, user, pid)
    base = f"/projects/{pid}/character-drafts/{did}"
    res = await client.post(f"{base}/discard", headers=h)
    assert res.json()["data"]["status"] == "discarded"
    assert code(await client.post(f"{base}/discard", headers=h)) == "DRAFT_ALREADY_RESOLVED"
    res = await client.patch(base, json={"character_name": "x"}, headers=h)
    assert code(res) == "DRAFT_ALREADY_RESOLVED"

    listed = await client.get(f"/projects/{pid}/character-drafts?status=pending_review", headers=h)
    assert listed.json()["data"] == []
    listed = await client.get(f"/projects/{pid}/character-drafts?status=discarded", headers=h)
    assert [d["draft_id"] for d in listed.json()["data"]] == [did]


async def test_character_crud_and_cursor(client: AsyncClient) -> None:
    user, pid = await _project(client, "ch6@example.com")
    h = user["headers"]
    ids = []
    for name in ("윤서", "재현"):
        did = await _draft(client, user, pid, name=name)
        res = await client.post(
            f"/projects/{pid}/character-drafts/{did}/confirm", json={}, headers=h
        )
        ids.append(res.json()["data"]["character_id"])

    page = (await client.get(f"/projects/{pid}/characters?limit=1", headers=h)).json()
    assert [c["character_id"] for c in page["data"]] == [ids[1]]
    rest = await client.get(
        f"/projects/{pid}/characters?limit=1&cursor={page['meta']['next_cursor']}", headers=h
    )
    assert [c["character_id"] for c in rest.json()["data"]] == [ids[0]]
    assert rest.json()["meta"]["next_cursor"] is None

    path = f"/projects/{pid}/characters/{ids[0]}"
    assert code(await client.patch(path, json={"name": "재현"}, headers=h)) == (
        "DUPLICATE_CHARACTER_CANDIDATE"
    )
    res = await client.patch(path, json={"name": "윤서하"}, headers=h)
    assert res.json()["data"]["name"] == "윤서하"
    history = (await client.get(path + "/edit-history", headers=h)).json()["data"]
    assert (history[-1]["action"], history[-1]["field"], history[-1]["before_value"]) == (
        "modified",
        "name",
        "윤서",
    )

    assert (await client.delete(path, headers=h)).status_code == 204
    assert code(await client.get(path, headers=h)) == "CHARACTER_NOT_FOUND"
    assert code(await client.get(path + "/edit-history", headers=h)) == "CHARACTER_NOT_FOUND"
    drafts = (await client.get(f"/projects/{pid}/character-drafts", headers=h)).json()["data"]
    assert {d["confirmed_character_id"] for d in drafts} == {None, ids[1]}  # 초안은 남는다


async def test_other_project_draft_is_not_found(client: AsyncClient) -> None:
    user, pid = await _project(client, "ch7@example.com")
    other = (
        await client.post(
            "/projects", json={"title": "Q", "owner_type": "personal"}, headers=user["headers"]
        )
    ).json()["data"]["project_id"]
    did = await _draft(client, user, pid)
    res = await client.get(f"/projects/{other}/character-drafts/{did}", headers=user["headers"])
    assert code(res) == "DRAFT_NOT_FOUND"
    res = await client.delete(
        f"/projects/{pid}/character-drafts/{did}/items/{UUID(int=1)}", headers=user["headers"]
    )
    assert code(res) == "ITEM_NOT_FOUND"


def test_edit_history_is_read_only() -> None:
    """append-only: 이력 경로에는 GET 만 있다."""
    for path, ops in app.openapi()["paths"].items():
        if path.endswith("/edit-history"):
            assert set(ops) == {"get"}, path
