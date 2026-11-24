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
    assert ai.calls[0] == ("run_rex", (BODY,)) or not results
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


async def test_repeated_extraction_merges_evidence_into_existing_rule(client: AsyncClient) -> None:
    m = await _manuscript(client, "rx-dedup@example.com")
    original = (
        await client.post(
            f"/projects/{m['pid']}/world-rules",
            json={
                "title": "기존 규칙",
                "description": RULES["data"]["extracted_rules"][0]["description"],
                "violation_keywords": [],
            },
            headers=m["h"],
        )
    ).json()["data"]
    eid = await _extract(client, m, RULES)
    candidates = (await client.get(f"{m['base']}/{eid}", headers=m["h"])).json()["data"][
        "extracted_rules"
    ]
    assert candidates[0]["duplicate_rule_id"] == original["rule_id"]
    assert candidates[0]["duplicate_kind"] == "exact"
    confirmed = await client.post(
        f"{m['base']}/{eid}/confirm", json={"selected_indices": [0]}, headers=m["h"]
    )
    [merged] = confirmed.json()["data"]
    assert merged["rule_id"] == original["rule_id"]
    assert merged["title"] == "기존 규칙" and merged["violation_keywords"] == []
    assert merged["evidence_sources"][0]["manuscript_id"] == m["mid"]
    assert merged["evidence_sources"][0]["quote"] == RULES["data"]["extracted_rules"][0]["evidence"]
    await client.post(f"{m['base']}/{eid}/confirm", json={"selected_indices": [0]}, headers=m["h"])
    listed = (await client.get(f"/projects/{m['pid']}/world-rules", headers=m["h"])).json()["data"]
    assert len(listed) == 1 and len(listed[0]["evidence_sources"]) == 1
    eid2 = await _extract(client, m, RULES)
    await client.post(f"{m['base']}/{eid2}/confirm", json={"selected_indices": [0]}, headers=m["h"])
    listed = (await client.get(f"/projects/{m['pid']}/world-rules", headers=m["h"])).json()["data"]
    assert len(listed) == 1 and len(listed[0]["evidence_sources"]) == 1


async def test_semantic_merge_requires_project_owned_target(client: AsyncClient) -> None:
    m = await _manuscript(client, "rx-merge@example.com")
    other = await _manuscript(client, "rx-merge-other@example.com")
    foreign = (
        await client.post(
            f"/projects/{other['pid']}/world-rules",
            json={"title": "다른 프로젝트", "description": "외부 규칙"},
            headers=other["h"],
        )
    ).json()["data"]
    eid = await _extract(client, m, RULES)
    response = await client.post(
        f"{m['base']}/{eid}/confirm",
        json={"selected_indices": [0], "merge_targets": {"0": foreign["rule_id"]}},
        headers=m["h"],
    )
    assert code(response) == "INVALID_INPUT"
    target = (
        await client.post(
            f"/projects/{m['pid']}/world-rules",
            json={"title": "우천 제한", "description": "강우가 멈춘 이후에만 붉은 문 개방 가능"},
            headers=m["h"],
        )
    ).json()["data"]
    merged = (
        await client.post(
            f"{m['base']}/{eid}/confirm",
            json={"selected_indices": [0], "merge_targets": {"0": target["rule_id"]}},
            headers=m["h"],
        )
    ).json()["data"]
    assert merged[0]["rule_id"] == target["rule_id"]
    assert merged[0]["description"] == target["description"]


async def test_two_extraction_confirms_do_not_race_into_duplicate_rules(
    client: AsyncClient,
) -> None:
    import asyncio

    m = await _manuscript(client, "rx-race@example.com")
    first = await _extract(client, m, RULES)
    second = await _extract(client, m, RULES)
    responses = await asyncio.gather(
        *[
            client.post(
                f"{m['base']}/{eid}/confirm", json={"selected_indices": [0]}, headers=m["h"]
            )
            for eid in (first, second)
        ]
    )
    assert all(r.status_code == 201 for r in responses)
    assert responses[0].json()["data"][0]["rule_id"] == responses[1].json()["data"][0]["rule_id"]
    rules = (await client.get(f"/projects/{m['pid']}/world-rules", headers=m["h"])).json()["data"]
    assert len(rules) == 1 and len(rules[0]["evidence_sources"]) == 1


async def test_project_history_restores_jobs_and_candidates_without_browser_ids(
    client: AsyncClient,
) -> None:
    m = await _manuscript(client, "restore-rules@example.com")
    path = f"/projects/{m['pid']}/rule-extractions"
    queued = (await client.post(m["base"], headers=m["h"])).json()["data"]
    restored = await client.get(path, headers=m["h"])
    assert restored.status_code == 200
    assert restored.json()["data"][0]["extraction_id"] == queued["extraction_id"]
    assert restored.json()["data"][0]["manuscript_id"] == m["mid"]
    assert restored.json()["data"][0]["status"] == "queued"
    await handle(SessionFactory, UUID(queued["extraction_id"]), FakeAI(RULES))
    completed = (await client.get(path, headers=m["h"])).json()["data"][0]
    assert completed["status"] == "completed"
    assert len(completed["extracted_rules"]) == 2
    assert completed["extracted_rules"][0]["review_status"] == "pending"
    await client.post(
        f"{m['base']}/{queued['extraction_id']}/confirm",
        headers=m["h"],
        json={"selected_indices": [0]},
    )
    reviewed = (await client.get(path, headers=m["h"])).json()["data"][0]
    assert reviewed["extracted_rules"][0]["review_status"] == "confirmed"
    stranger = await signup(client, "stranger-rules@example.com")
    assert (await client.get(path, headers=stranger["headers"])).status_code == 403
    await client.delete(f"/projects/{m['pid']}/manuscripts/{m['mid']}", headers=m["h"])
    assert (await client.get(path, headers=m["h"])).json()["data"] == []


async def test_repeated_concurrent_requests_reuse_active_job_and_only_emit_once(
    client: AsyncClient,
) -> None:
    import asyncio

    from sqlalchemy import text

    m = await _manuscript(client, "concurrent-rules@example.com")
    responses = await asyncio.gather(*(client.post(m["base"], headers=m["h"]) for _ in range(3)))
    assert all(r.status_code == 202 for r in responses)
    ids = {r.json()["data"]["extraction_id"] for r in responses}
    assert len(ids) == 1
    eid = ids.pop()
    async with SessionFactory() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM ops.outbox_events WHERE aggregate_id = :id"),
            {"id": UUID(eid)},
        )
        assert count == 1
    await handle(SessionFactory, UUID(eid), FakeAI(RULES))
    next_job = (await client.post(m["base"], headers=m["h"])).json()["data"]
    assert next_job["extraction_id"] != eid
    # Completed work remains available while a later extraction is queued.
    first = await client.get(f"/projects/{m['pid']}/rule-extractions?limit=1", headers=m["h"])
    assert first.json()["data"][0]["extraction_id"] == next_job["extraction_id"]
    cursor = first.json()["meta"]["next_cursor"]
    second = await client.get(
        f"/projects/{m['pid']}/rule-extractions",
        params={"limit": 1, "cursor": cursor},
        headers=m["h"],
    )
    assert second.json()["data"][0]["extraction_id"] == eid
    assert second.json()["meta"]["next_cursor"] is None


async def _chapter(client: AsyncClient, m: dict[str, Any], no: int, text: str) -> str:
    response = await client.post(
        f"/projects/{m['pid']}/chapters",
        headers=m["h"],
        json={"manuscript_id": m["mid"], "chapter_no": no, "content": text},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["chapter_id"])


async def test_scope_limit_boundary_and_manuscript_storage_unchanged(client: AsyncClient) -> None:
    from sqlalchemy import func, select

    from app.jobs.models import Job

    for size in (29999, 30000, 30001):
        # Include astral Unicode; frontend and server must not disagree about emoji length.
        text = "가" * (size - 1) + "😀"
        m = await _manuscript(client, f"rx-limit-{size}@example.com", text)
        cid = await _chapter(client, m, 1, text)
        response = await client.post(m["base"], headers=m["h"], json={"chapter_ids": [cid]})
        assert response.status_code == (202 if size <= 30000 else 400), response.text
        if size <= 30000:
            job = response.json()["data"]
            assert job["char_count"] == size
            assert job["chapter_numbers"] == [1]
            ai = FakeAI({"data": {"extracted_rules": []}})
            await handle(SessionFactory, UUID(job["extraction_id"]), ai)
            assert ai.calls == [("run_rex", (text,))]
        else:
            assert response.json()["error"]["details"]["char_count"] == size
            # The body-less legacy route cannot bypass the same cap.
            assert (await client.post(m["base"], headers=m["h"])).status_code == 400
            async with SessionFactory() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Job)
                        .where(Job.project_id == UUID(m["pid"]))
                    )
                    == 0
                )
        saved = (
            await client.get(f"/projects/{m['pid']}/manuscripts/{m['mid']}", headers=m["h"])
        ).json()["data"]
        assert saved["content"] == text
        saved_chapter = (
            await client.get(f"/projects/{m['pid']}/chapters/{cid}", headers=m["h"])
        ).json()["data"]
        assert saved_chapter["content"] == text


async def test_selected_scope_snapshot_retries_and_evidence_use_original_chapter_numbers(
    client: AsyncClient,
) -> None:
    import copy

    m = await _manuscript(client, "rx-snapshot@example.com", "전체 원고는 " + "가" * 60000)
    c2 = await _chapter(client, m, 2, "비가 멎자 문의 윤곽이 붉게 떠올랐다.")
    await _chapter(client, m, 4, "분석에서 제외할 비밀 " + "나" * 60000)
    c9 = await _chapter(client, m, 9, "기록 열람에는 위원 두 명의 승인이 필요했다.")
    body = {"chapter_ids": [c9, c2]}
    job = (await client.post(m["base"], headers=m["h"], json=body)).json()["data"]
    eid = job["extraction_id"]
    expected = "비가 멎자 문의 윤곽이 붉게 떠올랐다.\n\n기록 열람에는 위원 두 명의 승인이 필요했다."
    assert job["chapter_ids"] == [c2, c9] and job["chapter_numbers"] == [2, 9]
    assert job["char_count"] == len(expected)
    same = await client.post(m["base"], headers=m["h"], json={"chapter_ids": [c2, c9]})
    assert same.json()["data"]["extraction_id"] == eid
    different = await client.post(m["base"], headers=m["h"], json={"chapter_ids": [c2]})
    assert different.status_code == 400
    await client.patch(
        f"/projects/{m['pid']}/chapters/{c2}", headers=m["h"], json={"content": "수정" * 40000}
    )
    failed = FakeAI({"error": {"code": "AI_EXTRACTION_FAILED", "message": "test", "details": {}}})
    assert await handle(SessionFactory, UUID(eid), failed) == "failed"
    assert failed.calls == [("run_rex", (expected,))]
    assert (await client.post(f"{m['base']}/{eid}/retry", headers=m["h"])).status_code == 202
    ai = FakeAI(copy.deepcopy(RULES))
    assert await handle(SessionFactory, UUID(eid), ai) == "completed"
    assert ai.calls == [("run_rex", (expected,))]
    restored = (await client.get(f"/projects/{m['pid']}/rule-extractions", headers=m["h"])).json()[
        "data"
    ][0]
    assert restored["chapter_numbers"] == [2, 9]
    assert [c["source_chapter"] for c in restored["extracted_rules"]] == [2, 9]


async def test_invalid_selection_does_not_create_jobs(client: AsyncClient) -> None:
    from uuid import uuid4

    from sqlalchemy import func, select

    from app.jobs.models import Job

    m = await _manuscript(client, "rx-invalid-scope@example.com")
    other = await _manuscript(client, "rx-foreign-scope@example.com")
    local = await _chapter(client, m, 1, BODY)
    foreign = await _chapter(client, other, 1, "private")
    empty = await _chapter(client, m, 3, "  \n ")
    mid2 = (
        await client.post(
            f"/projects/{m['pid']}/manuscripts", headers=m["h"], json={"title": "다른 원고"}
        )
    ).json()["data"]["manuscript_id"]
    sibling = await _chapter(client, {**m, "mid": mid2}, 1, "같은 작품의 다른 원고")
    for ids in ([], [local, local], [str(uuid4())], [foreign], [sibling], [empty]):
        response = await client.post(m["base"], headers=m["h"], json={"chapter_ids": ids})
        assert response.status_code == 400, response.text
    async with SessionFactory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.job_type == "rule_extraction")
            )
            == 0
        )


async def test_multi_chapter_separator_counts_toward_limit(client: AsyncClient) -> None:
    m = await _manuscript(client, "rx-separators@example.com")
    a = await _chapter(client, m, 1, "가" * 15000)
    b = await _chapter(client, m, 2, "나" * 14999)
    response = await client.post(m["base"], headers=m["h"], json={"chapter_ids": [a, b]})
    assert response.status_code == 400
    assert response.json()["error"]["details"]["char_count"] == 30001
    await client.patch(
        f"/projects/{m['pid']}/chapters/{b}", headers=m["h"], json={"content": "나" * 14998}
    )
    response = await client.post(m["base"], headers=m["h"], json={"chapter_ids": [b, a]})
    assert response.status_code == 202
    assert response.json()["data"]["char_count"] == 30000


async def test_legacy_queued_job_over_limit_never_calls_ai(client: AsyncClient) -> None:
    from app.jobs import service as jobs

    m = await _manuscript(client, "rx-legacy-limit@example.com", "가" * 30001)
    async with SessionFactory() as session:
        job = await jobs.create(
            session,
            project_id=UUID(m["pid"]),
            job_type="rule_extraction",
            target_type="manuscript",
            target_id=UUID(m["mid"]),
            queue="ai",
        )
        await session.commit()
        eid = job.id
    ai = FakeAI(RULES)
    assert await handle(SessionFactory, eid, ai) == "failed"
    assert ai.calls == []
    response = await client.get(f"{m['base']}/{eid}", headers=m["h"])
    assert response.json()["error"]["code"] == "INVALID_INPUT"


async def test_scoped_request_cannot_reuse_legacy_full_manuscript_job(client: AsyncClient) -> None:
    from app.jobs import service as jobs

    m = await _manuscript(client, "rx-legacy-scope@example.com")
    cid = await _chapter(client, m, 1, BODY)
    async with SessionFactory() as session:
        await jobs.create(
            session,
            project_id=UUID(m["pid"]),
            job_type="rule_extraction",
            target_type="manuscript",
            target_id=UUID(m["mid"]),
            queue="ai",
        )
        await session.commit()
    response = await client.post(m["base"], headers=m["h"], json={"chapter_ids": [cid]})
    assert response.status_code == 400
