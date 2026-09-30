"""SSM — 구조 분석 잡 · 지도 · 노드 수정 · 다시 분석 · 하트비트(긴 잡)."""

import time
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.core.config import settings
from app.db.session import SessionFactory
from app.insight.ssm.service import handle
from app.jobs import service as jobs
from app.jobs.models import Job
from tests.conftest import FakeAI, code, signup


def structure(char: str | None = None, title: str = "벤 삼촌의 죽음") -> dict[str, Any]:
    chars = [char, "없는-캐릭터"] if char else []
    return {
        "data": {
            "acts": [{"act_name": "발단", "chapter_from": 1, "chapter_to": 2, "summary": "시작"}],
            "nodes": [
                {
                    "node_id": "node_1",
                    "type": "turning_point",
                    "chapter": 1,
                    "title": title,
                    "summary": "계기",
                    "character_ids": chars,
                },
                {
                    "node_id": "node_2",
                    "type": "event",
                    "chapter": 2,
                    "title": "첫 활동",
                    "summary": "결심",
                    "character_ids": [],
                },
            ],
            "edges": [{"from_node_id": "node_1", "to_node_id": "node_2", "relation": "causes"}],
        },
        "meta": {"removed_edge_count": 0},
    }


async def _manuscript(
    client: AsyncClient, email: str, content: str = "1장. 시작.\n2장. 결심."
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
    draft = (
        await client.post(
            f"/projects/{pid}/character-drafts", json={"character_name": "피터"}, headers=h
        )
    ).json()["data"]["draft_id"]
    char = (
        await client.post(f"/projects/{pid}/character-drafts/{draft}/confirm", json={}, headers=h)
    ).json()["data"]["character_id"]
    base = f"/projects/{pid}/manuscripts/{mid}"
    return {"h": h, "base": base, "char": char}


async def _analyze(client: AsyncClient, m: dict[str, Any], result: dict[str, Any]) -> str:
    res = await client.post(f"{m['base']}/structure-analyses", headers=m["h"])
    assert res.status_code == 202 and res.json()["data"]["status"] == "queued"
    aid = res.json()["data"]["analysis_id"]
    assert await handle(SessionFactory, UUID(aid), FakeAI(result)) == "completed"
    return str(aid)


async def test_analysis_map_and_node_edit(client: AsyncClient) -> None:
    m = await _manuscript(client, "ss1@example.com")
    h, base = m["h"], m["base"]
    assert code(await client.get(f"{base}/structure-map", headers=h)) == "STRUCTURE_MAP_NOT_FOUND"
    aid = await _analyze(client, m, structure(m["char"]))
    got = (await client.get(f"{base}/structure-analyses/{aid}", headers=h)).json()
    assert got["data"]["status"] == "completed" and got["error"] is None
    assert got["data"]["structure_map_ref"].endswith("/structure-map")

    smap = (await client.get(f"{base}/structure-map", headers=h)).json()["data"]
    assert smap["analysis_id"] == aid and smap["acts"][0]["act_name"] == "발단"
    first, second = smap["nodes"]
    assert (first["title"], first["chapter"], first["type"]) == (
        "벤 삼촌의 죽음",
        1,
        "turning_point",
    )
    assert first["character_ids"] == [m["char"]]  # 모르는 캐릭터 id 는 버린다
    assert smap["edges"] == [
        {"from_node_id": first["node_id"], "to_node_id": second["node_id"], "relation": "causes"}
    ]

    node = f"{base}/structure-map/nodes/{first['node_id']}"
    res = await client.patch(node, json={"summary": "책임감의 계기"}, headers=h)
    assert (res.json()["data"]["summary"], res.json()["data"]["is_user_edited"]) == (
        "책임감의 계기",
        True,
    )
    assert (await client.get(node, headers=h)).json()["data"]["summary"] == "책임감의 계기"
    missing = f"{base}/structure-map/nodes/{UUID(int=1)}"
    assert code(await client.get(missing, headers=h)) == "STRUCTURE_NODE_NOT_FOUND"


async def test_reanalysis_keeps_user_edited_nodes(client: AsyncClient) -> None:
    m = await _manuscript(client, "ss2@example.com")
    h, base = m["h"], m["base"]
    await _analyze(client, m, structure())
    nodes = (await client.get(f"{base}/structure-map", headers=h)).json()["data"]["nodes"]
    edited = nodes[0]["node_id"]
    await client.patch(
        f"{base}/structure-map/nodes/{edited}", json={"title": "내가 고친 제목"}, headers=h
    )

    second = await _analyze(client, m, structure(title="새 분석 제목"))
    smap = (await client.get(f"{base}/structure-map", headers=h)).json()["data"]
    assert smap["analysis_id"] == second
    titles = sorted(n["title"] for n in smap["nodes"])
    assert titles == [
        "내가 고친 제목",
        "새 분석 제목",
        "첫 활동",
    ]  # 고친 노드는 남고 AI 노드는 바뀐다
    assert {e["from_node_id"] for e in smap["edges"]} != {edited}


async def test_empty_manuscript_failure_and_retry(client: AsyncClient) -> None:
    m = await _manuscript(client, "ss3@example.com", content="")
    assert code(await client.post(f"{m['base']}/structure-analyses", headers=m["h"])) == (
        "MANUSCRIPT_TOO_SHORT"
    )
    m = await _manuscript(client, "ss4@example.com")
    h, base = m["h"], m["base"]
    aid = (await client.post(f"{base}/structure-analyses", headers=h)).json()["data"]["analysis_id"]
    failed = {
        "error": {
            "code": "AI_ANALYSIS_FAILED",
            "message": "실패",
            "details": {"chunk_index": 1, "chunk_count": 2},
        }
    }
    assert await handle(SessionFactory, UUID(aid), FakeAI(failed)) == "failed"
    got = (await client.get(f"{base}/structure-analyses/{aid}", headers=h)).json()
    assert got["error"]["details"] == {"chunk_index": 1, "chunk_count": 2}
    assert (
        await client.post(f"{base}/structure-analyses/{aid}/retry", headers=h)
    ).status_code == 202
    await handle(SessionFactory, UUID(aid), FakeAI(structure()))
    res = await client.post(f"{base}/structure-analyses/{aid}/retry", headers=h)
    assert code(res) == "INVALID_STATUS_TRANSITION"


class SlowAI(FakeAI):
    """챕터가 많은 장편처럼 오래 걸린다."""

    def run_ssm(self, manuscript_text: str, characters: Any = None) -> dict[str, Any]:
        time.sleep(0.5)
        return structure()


async def test_long_job_heartbeats(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    m = await _manuscript(client, "ss5@example.com")
    aid = (await client.post(f"{m['base']}/structure-analyses", headers=m["h"])).json()["data"][
        "analysis_id"
    ]
    monkeypatch.setattr(settings, "job_heartbeat_seconds", 0.1)
    assert await handle(SessionFactory, UUID(aid), SlowAI()) == "completed"
    async with SessionFactory() as s:
        beat = (await s.execute(select(Job.heartbeat_at).where(Job.id == UUID(aid)))).scalar_one()
    assert beat is not None


async def test_reap_judges_by_heartbeat(client: AsyncClient) -> None:
    m = await _manuscript(client, "ss6@example.com")
    aid = UUID(
        (await client.post(f"{m['base']}/structure-analyses", headers=m["h"])).json()["data"][
            "analysis_id"
        ]
    )
    old = func.now() - timedelta(hours=2)
    async with SessionFactory() as s:
        # 두 시간 전에 시작했지만 방금 하트비트를 찍었다 — 살아 있다
        await s.execute(
            update(Job)
            .where(Job.id == aid)
            .values(status="running", attempt=1, started_at=old, heartbeat_at=func.now())
        )
        await s.commit()
        assert await jobs.reap(s) == []
        # 하트비트가 끊겼다 — 좀비
        await s.execute(update(Job).where(Job.id == aid).values(heartbeat_at=old))
        await s.commit()
        reaped = await jobs.reap(s)
        await s.commit()
    assert [(j.id, o) for j, o in reaped] == [(aid, "retried")]
