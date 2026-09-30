"""REX — 세계관 규칙 직접 입력 경로(origin = user_added)."""

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import code, signup


async def _project(client: AsyncClient, email: str) -> tuple[dict, str]:
    user = await signup(client, email)
    res = await client.post(
        "/projects", json={"title": "P", "owner_type": "personal"}, headers=user["headers"]
    )
    return user, res.json()["data"]["project_id"]


async def test_rule_crud(client: AsyncClient, db: AsyncSession) -> None:
    user, pid = await _project(client, "wr1@example.com")
    h = user["headers"]
    res = await client.post(
        f"/projects/{pid}/world-rules",
        json={
            "title": "붉은 문",
            "description": "붉은 문은 비가 그친 뒤에만 열린다",
            "violation_keywords": ["비 오는 중에 열림", " 맑은 날 개방 ", "비 오는 중에 열림"],
        },
        headers=h,
    )
    assert res.status_code == 201, res.text
    rule = res.json()["data"]
    assert rule["origin"] == "user_added"
    assert rule["extraction_id"] is None and rule["evidence"] is None
    assert rule["violation_keywords"] == ["비 오는 중에 열림", "맑은 날 개방"]

    path = f"/projects/{pid}/world-rules/{rule['rule_id']}"
    res = await client.patch(path, json={"violation_keywords": ["문이 저절로 열림"]}, headers=h)
    assert res.json()["data"]["violation_keywords"] == ["문이 저절로 열림"]
    assert res.json()["data"]["title"] == "붉은 문"  # 보내지 않은 필드는 그대로

    # SCDS 룰 필터가 쓰는 GIN 경로로 찾힌다
    hit = await db.execute(
        text("SELECT id FROM authoring.world_rules WHERE violation_keywords && :kw"),
        {"kw": ["문이 저절로 열림"]},
    )
    assert [r[0] for r in hit] == [UUID(rule["rule_id"])]

    assert (await client.delete(path, headers=h)).status_code == 204
    assert code(await client.delete(path, headers=h)) == "WORLD_RULE_NOT_FOUND"
    assert code(await client.patch(path, json={"title": "x"}, headers=h)) == "WORLD_RULE_NOT_FOUND"


async def test_rules_list_oldest_first_with_cursor(client: AsyncClient) -> None:
    user, pid = await _project(client, "wr2@example.com")
    h = user["headers"]
    ids = []
    for title in ("R01", "R02", "R03"):
        res = await client.post(
            f"/projects/{pid}/world-rules", json={"title": title, "description": "d"}, headers=h
        )
        ids.append(res.json()["data"]["rule_id"])
    first = (await client.get(f"/projects/{pid}/world-rules?limit=2", headers=h)).json()
    assert [r["rule_id"] for r in first["data"]] == ids[:2]
    rest = await client.get(
        f"/projects/{pid}/world-rules?limit=2&cursor={first['meta']['next_cursor']}", headers=h
    )
    assert [r["rule_id"] for r in rest.json()["data"]] == ids[2:]


async def test_rule_requires_title_and_description(client: AsyncClient) -> None:
    user, pid = await _project(client, "wr3@example.com")
    res = await client.post(
        f"/projects/{pid}/world-rules", json={"description": "제목 없음"}, headers=user["headers"]
    )
    assert code(res) == "INVALID_INPUT"


async def test_other_project_rule_is_not_found(client: AsyncClient) -> None:
    user, pid = await _project(client, "wr4@example.com")
    other = (
        await client.post(
            "/projects", json={"title": "Q", "owner_type": "personal"}, headers=user["headers"]
        )
    ).json()["data"]["project_id"]
    rule = (
        await client.post(
            f"/projects/{pid}/world-rules",
            json={"title": "t", "description": "d"},
            headers=user["headers"],
        )
    ).json()["data"]
    res = await client.delete(
        f"/projects/{other}/world-rules/{rule['rule_id']}", headers=user["headers"]
    )
    assert code(res) == "WORLD_RULE_NOT_FOUND"
