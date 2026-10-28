"""실제 Postgres에서 RCV 저장·재조회, 원고 분리, 경합, 권한·참조 무결성을 검증한다."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform_.projects.models import ProjectMember
from tests.conftest import code, signup


async def setup(client: AsyncClient, email: str = "relations@example.com") -> dict[str, Any]:
    user = await signup(client, email)
    h = user["headers"]
    pid = (
        await client.post(
            "/projects", headers=h, json={"title": "관계 연동", "owner_type": "personal"}
        )
    ).json()["data"]["project_id"]
    base = f"/projects/{pid}"
    people = []
    for name in ["윤서", "재현", "민아"]:
        draft = (
            await client.post(base + "/character-drafts", headers=h, json={"character_name": name})
        ).json()["data"]["draft_id"]
        person = (
            await client.post(f"{base}/character-drafts/{draft}/confirm", headers=h, json={})
        ).json()["data"]
        people.append(person["character_id"])
    manuscripts, chapters = [], []
    for title in ["초고", "개정고"]:
        ms = (await client.post(base + "/manuscripts", headers=h, json={"title": title})).json()[
            "data"
        ]["manuscript_id"]
        manuscripts.append(ms)
        ids = {}
        for no in [1, 2, 3]:
            ids[no] = (
                await client.post(
                    base + "/chapters",
                    headers=h,
                    json={"manuscript_id": ms, "chapter_no": no, "content": f"{no}장의 원문"},
                )
            ).json()["data"]["chapter_id"]
        chapters.append(ids)
    return {
        "h": h,
        "pid": pid,
        "base": base,
        "people": people,
        "ms": manuscripts,
        "chapters": chapters,
    }


def body(s: dict, ms: int = 0, chapter: int = 1, target: int = 1) -> dict:
    return {
        "source_character_id": s["people"][0],
        "target_character_id": s["people"][target],
        "initial_history": {
            "chapter_id": s["chapters"][ms][chapter],
            "state": "신뢰",
            "trust": None,
            "event_title": "함께 기록을 찾았다",
        },
    }


async def create(client: AsyncClient, s: dict, **kw: Any) -> dict:
    res = await client.post(s["base"] + "/relationships", headers=s["h"], json=body(s, **kw))
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def test_persistence_snapshot_overwrite_and_delete(client: AsyncClient) -> None:
    s = await setup(client)
    h, base, ms = s["h"], s["base"], s["ms"][0]
    made = await create(client, s, chapter=2)
    path = f"{base}/relationships/{made['relationship_id']}"
    saved = (await client.get(path, headers=h, params={"manuscript_id": ms})).json()["data"]
    assert saved == made
    assert saved["history"][0]["trust"] is None
    snap = path + "/snapshot"
    before = (await client.get(snap, headers=h, params={"manuscript_id": ms, "chapter": 1})).json()[
        "data"
    ]
    assert before["state"] is None and before["resolved_chapter"] is None
    carried = (
        await client.get(snap, headers=h, params={"manuscript_id": ms, "chapter": 3})
    ).json()["data"]
    assert carried["state"] == "신뢰" and carried["is_carried_forward"] is True
    new = {"chapter_id": s["chapters"][0][3], "state": "갈등", "trust": 0}
    assert (await client.post(path + "/history", headers=h, json=new)).status_code == 201
    duplicate = await client.post(path + "/history", headers=h, json=new | {"state": "중립"})
    assert code(duplicate) == "DUPLICATE_CHAPTER_RECORD"
    assert duplicate.json()["error"]["details"]["existing_entry"]["trust"] == 0
    assert (
        await client.post(
            path + "/history?overwrite=true", headers=h, json=new | {"state": "중립", "trust": None}
        )
    ).status_code == 201
    graph = (
        await client.get(
            base + "/relationship-mindmap", headers=h, params={"manuscript_id": ms, "chapter": 3}
        )
    ).json()["data"]
    assert len(graph["nodes"]) == 2 and graph["edges"][0]["state"] == "중립"
    assert graph["edges"][0]["is_carried_forward"] is False
    assert (
        code(await client.delete(base + "/characters/" + s["people"][0], headers=h))
        == "CHARACTER_HAS_DEPENDENT_RELATIONSHIPS"
    )
    ref = path + "/history/" + s["chapters"][0][3]
    assert (await client.delete(ref, headers=h)).status_code == 204
    assert code(await client.delete(ref, headers=h)) == "HISTORY_ENTRY_NOT_FOUND"
    assert (await client.delete(path, headers=h)).status_code == 204
    assert code(await client.get(path, headers=h)) == "RELATIONSHIP_NOT_FOUND"
    assert (
        await client.delete(base + "/characters/" + s["people"][0], headers=h)
    ).status_code == 204


async def test_same_chapter_number_in_two_manuscripts(client: AsyncClient) -> None:
    s = await setup(client)
    first = await create(client, s)
    second = await create(client, s, ms=1)
    assert first["relationship_id"] == second["relationship_id"]
    path = f"{s['base']}/relationships/{first['relationship_id']}"
    await client.post(
        path + "/history?overwrite=true",
        headers=s["h"],
        json={"chapter_id": s["chapters"][1][1], "state": "적대", "trust": 4},
    )
    for idx, expected in [(0, "신뢰"), (1, "적대")]:
        rows = (
            await client.get(
                s["base"] + "/relationships", headers=s["h"], params={"manuscript_id": s["ms"][idx]}
            )
        ).json()["data"]
        assert len(rows[0]["history"]) == 1
        assert rows[0]["history"][0]["state"] == expected
        assert rows[0]["history"][0]["chapter_id"] == s["chapters"][idx][1]
    # 챕터 삭제는 그 원고의 기록만 지우며 다른 원고에 영향을 주지 않는다.
    await client.delete(s["base"] + "/chapters/" + s["chapters"][0][1], headers=s["h"])
    saved = (await client.get(path, headers=s["h"])).json()["data"]
    assert [e["state"] for e in saved["history"]] == ["적대"]


async def test_permissions_and_foreign_references(client: AsyncClient, db: AsyncSession) -> None:
    s = await setup(client)
    foreign = await setup(client, "foreign@example.com")
    base, h = s["base"] + "/relationships", s["h"]
    assert (await client.get(base)).status_code == 401
    assert (await client.get(base, headers=foreign["h"])).status_code == 403
    assert (
        code(
            await client.post(
                base, headers=h, json=body(s) | {"target_character_id": foreign["people"][0]}
            )
        )
        == "CHARACTER_NOT_FOUND"
    )
    invalid = body(s)
    invalid["initial_history"]["chapter_id"] = foreign["chapters"][0][1]
    assert code(await client.post(base, headers=h, json=invalid)) == "CHAPTER_NOT_FOUND"
    assert (
        code(await client.get(base, headers=h, params={"manuscript_id": foreign["ms"][0]}))
        == "MANUSCRIPT_NOT_FOUND"
    )
    made = await create(client, s)
    wrong = f"{foreign['base']}/relationships/{made['relationship_id']}"
    assert code(await client.get(wrong, headers=foreign["h"])) == "RELATIONSHIP_NOT_FOUND"
    viewer = await signup(client, "viewer@example.com")
    db.add(
        ProjectMember(
            project_id=UUID(s["pid"]),
            user_id=UUID(viewer["id"]),
            role="viewer",
            joined_at=func.now(),
        )
    )
    await db.commit()
    assert (await client.get(base, headers=viewer["headers"])).status_code == 200
    assert (await client.post(base, headers=viewer["headers"], json=body(s))).status_code == 403
    assert (
        await client.delete(base + "/" + made["relationship_id"], headers=viewer["headers"])
    ).status_code == 403


async def test_pagination_validation_and_concurrent_writes(client: AsyncClient) -> None:
    s = await setup(client)
    base, h = s["base"] + "/relationships", s["h"]
    invalid = body(s)
    invalid["initial_history"]["trust"] = 101
    assert code(await client.post(base, headers=h, json=invalid)) == "INVALID_INPUT"
    assert (
        code(
            await client.post(
                base, headers=h, json=body(s) | {"target_character_id": s["people"][0]}
            )
        )
        == "INVALID_INPUT"
    )
    forward, reverse = body(s), body(s)
    reverse["source_character_id"], reverse["target_character_id"] = s["people"][1], s["people"][0]
    outcomes = await asyncio.gather(
        client.post(base, headers=h, json=forward), client.post(base, headers=h, json=reverse)
    )
    assert sorted(r.status_code for r in outcomes) == [201, 409]
    made = next(r.json()["data"] for r in outcomes if r.status_code == 201)
    await create(client, s, target=2)
    page = (await client.get(base + "?limit=1", headers=h)).json()
    other = (
        await client.get(
            base, headers=h, params={"limit": 1, "cursor": page["meta"]["next_cursor"]}
        )
    ).json()
    assert other["data"][0]["relationship_id"] != page["data"][0]["relationship_id"]
    assert other["meta"]["next_cursor"] is None
    path = base + "/" + made["relationship_id"] + "/history"
    new = {"chapter_id": s["chapters"][0][2], "state": "중립"}
    outcomes = await asyncio.gather(
        client.post(path, headers=h, json=new), client.post(path, headers=h, json=new)
    )
    assert sorted(r.status_code for r in outcomes) == [201, 409]
    assert code(await client.get(base + "/" + str(uuid4()), headers=h)) == "RELATIONSHIP_NOT_FOUND"
