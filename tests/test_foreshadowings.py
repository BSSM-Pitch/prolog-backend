"""FTS — 복선 설치·연결·회수, 미회수 안내, 타임라인, 챕터 삭제 → orphaned.

챕터 삭제는 실제 경로를 탄다: MSU 가 outbox 에 `chapter.deleted` 를 남기고, 릴레이와 같은 함수로
메시지를 만들어 이벤트 워커(`worker.notifier.handle`)에 넣는다.
"""

from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.models import OutboxEvent
from app.events.relay import message
from tests.conftest import code, signup
from worker.notifier import handle


async def _setup(client: AsyncClient, email: str, chapters: int = 5) -> dict[str, Any]:
    user = await signup(client, email)
    h = user["headers"]
    pid = (
        await client.post("/projects", json={"title": "P", "owner_type": "personal"}, headers=h)
    ).json()["data"]["project_id"]
    ms = (
        await client.post(f"/projects/{pid}/manuscripts", json={"title": "원고"}, headers=h)
    ).json()["data"]["manuscript_id"]
    ids = {}
    for no in range(1, chapters + 1):
        res = await client.post(
            f"/projects/{pid}/chapters",
            json={"manuscript_id": ms, "chapter_no": no * 5},  # 5, 10, 15, …
            headers=h,
        )
        ids[no * 5] = res.json()["data"]["chapter_id"]
    return {"h": h, "pid": pid, "ms": ms, "ch": ids, "base": f"/projects/{pid}/foreshadowings"}


async def _make(client: AsyncClient, s: dict[str, Any], title: str, setup: int, **kw: Any) -> dict:
    body = {"title": title, "setup_chapter_id": s["ch"][setup]} | kw
    res = await client.post(s["base"], json=body, headers=s["h"])
    assert res.status_code == 201, res.text
    return res.json()


async def _deliver_chapter_deletions(db: AsyncSession) -> None:
    """릴레이 대신: 쌓인 chapter.deleted 를 워커에 한 번씩(두 번째는 중복 수신)."""
    rows = (
        await db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "chapter.deleted"))
    ).scalars()
    for event in rows:
        await handle(message(event))
        await handle(message(event))  # at-least-once — 두 번 와도 한 번만


async def test_setup_link_payoff_flow(client: AsyncClient) -> None:
    s = await _setup(client, "fs1@example.com")
    h, base = s["h"], s["base"]
    created = await _make(client, s, "붉은 열쇠 모양의 흉터", 10)
    f = created["data"]
    assert (f["status"], f["setup_chapter"], f["payoff_chapter"]) == ("unresolved", 10, None)
    assert f["chapters"] == [{"chapter_id": s["ch"][10], "chapter_no": 10, "role": "setup"}]
    assert created["meta"] == {"similar_candidates": []}
    path = f"{base}/{f['foreshadowing_id']}"

    # 중복 복선은 막지 않고 후보만 알린다
    twin = await _make(client, s, "붉은 열쇠", 15)
    assert [c["foreshadowing_id"] for c in twin["meta"]["similar_candidates"]] == [
        f["foreshadowing_id"]
    ]

    res = await client.post(f"{path}/linked-chapters", json={"chapter_id": s["ch"][5]}, headers=h)
    assert code(res) == "INVALID_INPUT"  # 설치(10)보다 앞선다
    for no in (20, 15, 20):  # 같은 챕터를 두 번 보내도 한 번
        res = await client.post(
            f"{path}/linked-chapters", json={"chapter_id": s["ch"][no]}, headers=h
        )
        assert res.status_code == 201
    assert res.json()["data"]["linked_chapters"] == [15, 20]

    res = await client.put(f"{path}/payoff", json={"payoff_chapter_id": s["ch"][5]}, headers=h)
    assert res.status_code == 400 and code(res) == "INVALID_PAYOFF_CHAPTER"
    assert res.json()["error"]["details"] == {"setup_chapter": 10, "requested_payoff_chapter": 5}

    res = await client.put(f"{path}/payoff", json={"payoff_chapter_id": s["ch"][25]}, headers=h)
    assert (res.json()["data"]["status"], res.json()["data"]["payoff_chapter"]) == ("resolved", 25)
    # 설치를 회수 뒤로 옮길 수 없다
    res = await client.patch(path, json={"setup_chapter_id": s["ch"][25]}, headers=h)
    assert res.status_code == 200  # 25 == 25 는 허용
    res = await client.patch(path, json={"setup_chapter_id": s["ch"][10]}, headers=h)

    res = await client.delete(f"{path}/payoff", headers=h)
    assert (res.json()["data"]["status"], res.json()["data"]["payoff_chapter"]) == (
        "unresolved",
        None,
    )
    assert code(await client.delete(f"{path}/payoff", headers=h)) == "PAYOFF_NOT_SET"

    linked = f"{path}/linked-chapters/{s['ch'][15]}"
    assert (await client.delete(linked, headers=h)).status_code == 204
    assert code(await client.delete(linked, headers=h)) == "LINKED_CHAPTER_NOT_FOUND"

    assert (await client.delete(path, headers=h)).status_code == 204
    assert code(await client.get(path, headers=h)) == "FORESHADOWING_NOT_FOUND"


async def test_unresolved_advisories_and_timeline(client: AsyncClient) -> None:
    s = await _setup(client, "fs2@example.com", chapters=8)  # 5 … 40
    h, base, pid = s["h"], s["base"], s["pid"]
    old = (await _make(client, s, "재현의 비어 있는 학창 시절", 5))["data"]
    await client.post(
        f"{base}/{old['foreshadowing_id']}/linked-chapters",
        json={"chapter_id": s["ch"][20]},
        headers=h,
    )
    mid = (await _make(client, s, "새벽 3시 17분 열차", 25))["data"]
    done = (await _make(client, s, "두 번째 기록 원장", 10))["data"]
    await client.put(
        f"{base}/{done['foreshadowing_id']}/payoff",
        json={"payoff_chapter_id": s["ch"][35]},
        headers=h,
    )
    await _make(client, s, "아직 설치 전", 40)  # 현재(35)보다 뒤 — 경과가 없다

    res = await client.get(f"{base}/unresolved?current_chapter=35", headers=h)
    assert res.json()["data"] == {
        "current_chapter": 35,
        "unresolved": [
            {
                "foreshadowing_id": old["foreshadowing_id"],
                "title": old["title"],
                "setup_chapter": 5,
                "elapsed_chapters": 30,
            },
            {
                "foreshadowing_id": mid["foreshadowing_id"],
                "title": mid["title"],
                "setup_chapter": 25,
                "elapsed_chapters": 10,
            },
        ],
    }
    assert code(await client.get(f"{base}/unresolved", headers=h)) == "INVALID_INPUT"

    adv = (await client.get(f"{base}/unresolved/advisories?current_chapter=35", headers=h)).json()
    assert [(a["priority"], a["latest_linked_chapter"]) for a in adv["data"]] == [
        ("high", 20),
        ("medium", None),
    ]
    assert adv["data"][0]["message"].startswith("5장에 설치한 '재현의 비어 있는 학창 시절' 복선이")

    tracks = (await client.get(f"/projects/{pid}/foreshadowing-timeline", headers=h)).json()["data"]
    by_title = {t["title"]: t for t in tracks}
    assert by_title["두 번째 기록 원장"]["markers"] == [
        {"chapter": 10, "type": "setup"},
        {"chapter": 35, "type": "payoff"},
    ]
    assert by_title["두 번째 기록 원장"]["is_open"] is False
    assert by_title[old["title"]]["markers"][-1] == {"chapter": 20, "type": "linked"}
    assert by_title[old["title"]]["is_open"] is True

    # 범위 필터: 30~35 와 겹치는 것 — 열린 트랙(5~)·(25~)·(10~35), 40 설치는 제외
    ranged = await client.get(f"{base}?chapter_from=30&chapter_to=35", headers=h)
    assert len(ranged.json()["data"]) == 3
    resolved = await client.get(f"{base}?status=resolved", headers=h)
    assert [f["title"] for f in resolved.json()["data"]] == ["두 번째 기록 원장"]


async def test_chapter_deletion_orphans_and_unresolves(
    client: AsyncClient, db: AsyncSession
) -> None:
    s = await _setup(client, "fs3@example.com")
    h, base, pid = s["h"], s["base"], s["pid"]
    f = (await _make(client, s, "붉은 흉터", 10))["data"]
    path = f"{base}/{f['foreshadowing_id']}"
    await client.post(f"{path}/linked-chapters", json={"chapter_id": s["ch"][15]}, headers=h)
    await client.put(f"{path}/payoff", json={"payoff_chapter_id": s["ch"][20]}, headers=h)

    # 삭제 전 확인(명세 §4.15)
    refs = await client.get(f"/projects/{pid}/chapters/{s['ch'][10]}/foreshadowings", headers=h)
    assert [(r["title"], r["role"]) for r in refs.json()["data"]] == [("붉은 흉터", "setup")]

    # 회수 챕터 삭제 → 회수 취소와 같다
    assert (
        await client.delete(f"/projects/{pid}/chapters/{s['ch'][20]}", headers=h)
    ).status_code == 204
    await _deliver_chapter_deletions(db)
    now = (await client.get(path, headers=h)).json()["data"]
    assert (now["status"], now["payoff_chapter"]) == ("unresolved", None)

    # 설치 챕터 삭제 → orphaned. 자동 재지정하지 않는다
    await client.delete(f"/projects/{pid}/chapters/{s['ch'][10]}", headers=h)
    await _deliver_chapter_deletions(db)
    now = (await client.get(path, headers=h)).json()["data"]
    assert (now["status"], now["setup_chapter"], now["linked_chapters"]) == ("orphaned", None, [15])
    unresolved = await client.get(f"{base}/unresolved?current_chapter=25", headers=h)
    assert unresolved.json()["data"]["unresolved"] == []  # 설치가 없으니 경과도 없다

    # 사용자가 다시 지정하면 풀린다
    res = await client.patch(path, json={"setup_chapter_id": s["ch"][5]}, headers=h)
    assert (res.json()["data"]["status"], res.json()["data"]["setup_chapter"]) == ("unresolved", 5)

    # 원고 삭제도 챕터마다 이벤트를 남긴다 — 남은 챕터(5·15)가 사라진다
    await client.delete(f"/projects/{pid}/manuscripts/{s['ms']}", headers=h)
    await _deliver_chapter_deletions(db)
    now = (await client.get(path, headers=h)).json()["data"]
    assert (now["status"], now["chapters"]) == ("orphaned", [])


async def test_character_links(client: AsyncClient) -> None:
    s = await _setup(client, "fs4@example.com", chapters=2)
    h, base, pid = s["h"], s["base"], s["pid"]
    draft = (
        await client.post(
            f"/projects/{pid}/character-drafts", json={"character_name": "윤서"}, headers=h
        )
    ).json()["data"]["draft_id"]
    char = (
        await client.post(f"/projects/{pid}/character-drafts/{draft}/confirm", json={}, headers=h)
    ).json()["data"]["character_id"]

    f = (await _make(client, s, "흉터", 5, linked_character_ids=[char]))["data"]
    assert f["linked_character_ids"] == [char]
    path = f"{base}/{f['foreshadowing_id']}"
    res = await client.post(
        f"{path}/links",
        json={"target_type": "character", "target_id": str(UUID(int=1))},
        headers=h,
    )
    assert code(res) == "LINK_TARGET_NOT_FOUND"
    res = await client.post(
        f"{path}/links", json={"target_type": "event", "target_id": char}, headers=h
    )
    assert code(res) == "INVALID_INPUT"  # 사건 연결은 SCDS 이후

    filtered = await client.get(f"{base}?linked_character_id={char}", headers=h)
    assert [x["foreshadowing_id"] for x in filtered.json()["data"]] == [f["foreshadowing_id"]]

    # 캐릭터가 삭제되면 연결만 풀리고 복선은 남는다(명세 12항)
    await client.delete(f"/projects/{pid}/characters/{char}", headers=h)
    assert (await client.get(path, headers=h)).json()["data"]["linked_character_ids"] == []

    assert (await client.delete(f"{path}/links/character/{char}", headers=h)).status_code == 204


async def test_setup_chapter_must_belong_to_project(client: AsyncClient) -> None:
    a = await _setup(client, "fs5@example.com", chapters=1)
    b = await _setup(client, "fs6@example.com", chapters=1)
    res = await client.post(
        a["base"], json={"title": "t", "setup_chapter_id": b["ch"][5]}, headers=a["h"]
    )
    assert code(res) == "INVALID_INPUT"
    assert res.json()["error"]["details"] == {"field": "setup_chapter_id"}
