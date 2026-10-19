"""원고 기억 1단계 — 낡은 챕터 색인(스위퍼 · passage_index 잡) · 검색."""

import hashlib
from datetime import timedelta
from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select, text

from app.content.memory import service as memory
from app.content.memory.job_handler import handle
from app.content.memory.models import EMBEDDING_DIM, Passage
from app.db.session import SessionFactory
from app.jobs.models import Job
from tests.conftest import FakeAI, signup

LONG = "\n".join(f"{i}번 문단. " + "윤서는 문을 열었다. " * 30 for i in range(6))


def unit(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


async def _chapter(client: AsyncClient, email: str, content: str = LONG) -> dict[str, Any]:
    user = await signup(client, email)
    h = user["headers"]
    pid = (
        await client.post("/projects", json={"title": "P", "owner_type": "personal"}, headers=h)
    ).json()["data"]["project_id"]
    mid = (
        await client.post(f"/projects/{pid}/manuscripts", json={"title": "원고"}, headers=h)
    ).json()["data"]["manuscript_id"]
    cid = (
        await client.post(
            f"/projects/{pid}/chapters",
            json={"manuscript_id": mid, "chapter_no": 1, "title": "시작", "content": content},
            headers=h,
        )
    ).json()["data"]["chapter_id"]
    return {"h": h, "pid": pid, "mid": UUID(mid), "cid": UUID(cid)}


async def _age(cid: UUID, seconds: int = 600) -> None:
    """챕터를 조용한 지 오래된 것으로 만든다(트리거가 updated_at 을 덮으므로 끄고 쓴다)."""
    async with SessionFactory() as s:
        await s.execute(text("ALTER TABLE content.chapters DISABLE TRIGGER touch_updated_at"))
        await s.execute(
            text(
                "UPDATE content.chapters"
                " SET updated_at = now() - make_interval(secs => :s) WHERE id = :c"
            ),
            {"s": float(seconds), "c": cid},
        )
        await s.execute(text("ALTER TABLE content.chapters ENABLE TRIGGER touch_updated_at"))
        await s.commit()


async def _sweep() -> list[UUID]:
    async with SessionFactory() as s:
        made = await memory.sweep_stale(s, timedelta(seconds=180))
        await s.commit()
    return made


async def _job_for(cid: UUID) -> UUID:
    async with SessionFactory() as s:
        return (
            (
                await s.execute(
                    select(Job.id)
                    .where(Job.target_id == cid, Job.job_type == memory.JOB_TYPE)
                    .order_by(Job.created_at.desc())
                )
            )
            .scalars()
            .first()
        )


async def test_sweep_waits_for_quiet_and_makes_one_job(client: AsyncClient) -> None:
    c = await _chapter(client, "mem1@example.com")
    assert await _sweep() == []  # 방금 저장했다
    await _age(c["cid"])
    assert await _sweep() == [c["cid"]]
    assert await _sweep() == []  # 같은 본문으로는 잡이 하나뿐(idempotency_key)


async def test_index_stores_fresh_passages_and_reuses_embeddings(client: AsyncClient) -> None:
    c = await _chapter(client, "mem2@example.com")
    await _age(c["cid"])
    await _sweep()
    ai = FakeAI()  # split_passages 는 실제 패키지, embed 는 결정적 가짜
    assert await handle(SessionFactory, await _job_for(c["cid"]), ai) == "completed"

    async with SessionFactory() as s:
        rows = list(
            (await s.execute(select(Passage).where(Passage.chapter_id == c["cid"]))).scalars()
        )
        assert len(rows) > 1
        assert {r.source_hash for r in rows} == {hashlib.md5(LONG.encode()).hexdigest()}
        assert await memory.coverage(s, c["mid"]) == (1, 1)
        job = await s.get(Job, await _job_for(c["cid"]))
        assert job is not None and job.result is not None
        assert job.result["passage_count"] == len(rows)
        assert "passages" not in job.result  # 임베딩을 잡 테이블에 남기지 않는다
    first_embed = [args for name, args in ai.calls if name == "embed"]
    assert len(first_embed[0][0]) == len(rows)

    # 마지막 문단만 바꾸면 바뀐 조각만 다시 임베딩한다
    changed = LONG + "\n새로 쓴 마지막 문단."
    await client.patch(
        f"/projects/{c['pid']}/chapters/{c['cid']}", json={"content": changed}, headers=c["h"]
    )
    async with SessionFactory() as s:
        assert await memory.coverage(s, c["mid"]) == (0, 1)
    await _age(c["cid"])
    await _sweep()
    ai2 = FakeAI()
    assert await handle(SessionFactory, await _job_for(c["cid"]), ai2) == "completed"
    second_embed = [args for name, args in ai2.calls if name == "embed"]
    assert 0 < len(second_embed[0][0]) < len(rows)


async def test_embed_failure_leaves_chapter_stale_without_new_job(client: AsyncClient) -> None:
    c = await _chapter(client, "mem3@example.com")
    await _age(c["cid"])
    await _sweep()
    failure = {"error": {"code": "AI_EMBEDDING_FAILED", "message": "x", "details": {}}}
    ai = FakeAI(failure)
    assert await handle(SessionFactory, await _job_for(c["cid"]), ai) == "failed"
    async with SessionFactory() as s:
        job = await s.get(Job, await _job_for(c["cid"]))
        assert job is not None and job.status == "failed"
    assert await _sweep() == []  # 같은 본문으로 실패한 잡은 다시 만들지 않는다


async def test_search_orders_by_similarity_and_skips_stale(client: AsyncClient) -> None:
    c = await _chapter(client, "mem4@example.com")
    async with SessionFactory() as s:
        source = hashlib.md5(LONG.encode()).hexdigest()
        for seq, (body, hash_) in enumerate([("가", source), ("나", source), ("다", "낡음")]):
            s.add(
                Passage(
                    project_id=UUID(c["pid"]),
                    manuscript_id=c["mid"],
                    chapter_id=c["cid"],
                    seq=seq,
                    start_offset=seq,
                    end_offset=seq + 1,
                    text=body,
                    text_hash=body,
                    source_hash=hash_,
                    embedding=unit(seq),
                )
            )
        await s.commit()
        hits = await memory.search(s, c["mid"], unit(1), k=5)
    assert [h.text for h in hits] == ["나", "가"]  # 낡은 "다"는 빠진다
    assert hits[0].chapter_no == 1


async def test_blank_chapter_is_not_indexed_and_not_counted(client: AsyncClient) -> None:
    c = await _chapter(client, "mem5@example.com")
    blank = (
        await client.post(
            f"/projects/{c['pid']}/chapters",
            json={
                "manuscript_id": str(c["mid"]),
                "chapter_no": 2,
                "title": "빈",
                "content": "\n\t \n",
            },
            headers=c["h"],
        )
    ).json()["data"]["chapter_id"]
    await _age(c["cid"])
    await _age(UUID(blank))
    assert await _sweep() == [c["cid"]]  # 빈 챕터에는 잡을 만들지 않는다
    assert await handle(SessionFactory, await _job_for(c["cid"]), FakeAI()) == "completed"
    async with SessionFactory() as s:
        assert await memory.coverage(s, c["mid"]) == (1, 1)
