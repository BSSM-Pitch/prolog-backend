import copy
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.ai.narrative_review import (
    Review,
    SourceReview,
    indexed_source,
    resolve_sources,
    run,
    validate_evidence,
)
from app.db.session import SessionFactory
from app.insight.scds.service import handle
from tests.conftest import FakeAI
from tests.test_manuscripts import _manuscript, _project


async def book(client: AsyncClient) -> tuple[dict, str, dict, list[dict]]:
    user, pid = await _project(client, "book@example.com")
    manuscript = await _manuscript(client, user, pid)
    chapters = []
    for n, content in [
        (1, "서고는 두 열쇠를 동시에 돌려야 열린다."),
        (100, "문지기는 자기 열쇠 하나만으로 서고를 열었다."),
    ]:
        response = await client.post(
            f"/projects/{pid}/chapters",
            headers=user["headers"],
            json={
                "manuscript_id": manuscript["manuscript_id"],
                "chapter_no": n,
                "content": content,
            },
        )
        assert response.status_code == 201, response.text
        chapters.append(response.json()["data"])
    return user, pid, manuscript, chapters


def verdict(chapters: list[dict]) -> dict:
    evidence = [
        {k: c[k] for k in ("chapter_id", "chapter_no")} | {"quote": c["content"]} for c in chapters
    ]
    return {
        "summary": "두 열쇠 규칙과 후반 행동이 충돌합니다.",
        "more_items": False,
        "tracks": [
            {
                "title": "서고의 열쇠",
                "kind": "setting",
                "status": "established",
                "explanation": "처음 제시된 조건",
                "evidence": evidence[:1],
            }
        ],
        "findings": [
            {
                "title": "열쇠 수 충돌",
                "category": "contradiction",
                "severity": "high",
                "reason": "두 열쇠를 요구하지만 하나만 사용했습니다.",
                "evidence": evidence,
            }
        ],
    }


async def test_no_rules_full_source_durable_idempotent_and_two_sided_evidence(client: AsyncClient):
    user, pid, m, cs = await book(client)
    url = f"/projects/{pid}/manuscripts/{m['manuscript_id']}/narrative-reviews"
    assert (await client.get(url + "/latest", headers=user["headers"])).json()["data"] is None
    first = await client.post(url, headers=user["headers"])
    assert first.status_code == 202, first.text
    job = first.json()["data"]
    assert job["status"] == "queued" and job["analysis_kind"] == "narrative_review"
    assert job["narrative"]["chapter_count"] == 2
    second = await client.post(url, headers=user["headers"])
    assert second.json()["data"]["check_id"] == job["check_id"]
    ai = FakeAI({"data": verdict(cs)})
    assert await handle(SessionFactory, UUID(job["check_id"]), ai) == "completed"
    sent = ai.calls[0][1][0]
    assert [c["chapter_no"] for c in sent["chapters"]] == [1, 100]
    assert sent["saved_rules"] == []
    result = (await client.get(url + "/latest", headers=user["headers"])).json()["data"]
    assert result["status"] == "completed" and not result["narrative"]["stale"]
    assert len(result["conflict_ids"]) == 1
    conflict = (await client.get(f"/projects/{pid}/conflicts", headers=user["headers"])).json()[
        "data"
    ][0]
    assert conflict["chapter_id"] == cs[-1]["chapter_id"]
    assert [e["chapter_no"] for e in conflict["source_evidence"]] == [1, 100]
    assert conflict["rule_id"] is None and not conflict["review_stale"]
    assert (await client.get(f"/projects/{pid}/world-rules", headers=user["headers"])).json()[
        "data"
    ] == []

    # Early-condition edits invalidate late findings even when their anchor is unchanged.
    await client.patch(
        f"/projects/{pid}/chapters/{cs[0]['chapter_id']}",
        headers=user["headers"],
        json={"content": "서고는 문지기의 열쇠 하나로도 열린다."},
    )
    stale = (await client.get(url + "/latest", headers=user["headers"])).json()["data"]
    assert stale["narrative"]["stale"]
    conflict_url = f"/projects/{pid}/conflicts/{conflict['conflict_id']}"
    assert (await client.get(conflict_url, headers=user["headers"])).json()["data"]["review_stale"]
    edit = await client.patch(
        conflict_url,
        headers=user["headers"],
        json={
            "action": "modified",
            "source_hash": conflict["source_hash"],
            "modified_content": "잘못된 수정",
        },
    )
    assert edit.status_code == 400
    next_job = (await client.post(url, headers=user["headers"])).json()["data"]
    assert next_job["check_id"] != job["check_id"]


async def test_changes_during_analysis_do_not_publish_stale_conflicts(client: AsyncClient):
    user, pid, m, cs = await book(client)
    url = f"/projects/{pid}/manuscripts/{m['manuscript_id']}/narrative-reviews"
    job = (await client.post(url, headers=user["headers"])).json()["data"]
    await client.patch(
        f"/projects/{pid}/chapters/{cs[0]['chapter_id']}",
        headers=user["headers"],
        json={"content": "서고 규칙이 개정되었다."},
    )
    assert (
        await handle(SessionFactory, UUID(job["check_id"]), FakeAI({"data": verdict(cs)}))
        == "completed"
    )
    result = (await client.get(url + "/latest", headers=user["headers"])).json()["data"]
    assert result["narrative"]["stale"] and not result["conflict_ids"]


async def test_gaps_and_unresolved_threads_are_not_definite_conflicts(client: AsyncClient):
    user, pid, m, cs = await book(client)
    data = verdict(cs)
    data["findings"][0]["category"] = "explanation_gap"
    data["tracks"][0].update(kind="foreshadowing", status="unresolved")
    url = f"/projects/{pid}/manuscripts/{m['manuscript_id']}/narrative-reviews"
    job = (await client.post(url, headers=user["headers"])).json()["data"]
    assert (
        await handle(SessionFactory, UUID(job["check_id"]), FakeAI({"data": data})) == "completed"
    )
    result = (await client.get(url + "/latest", headers=user["headers"])).json()["data"]
    assert result["narrative"]["tracks"][0]["status"] == "unresolved"
    assert not result["conflict_ids"]


def test_complete_long_input_and_final_review_override():
    import json

    cs = [
        {"chapter_id": str(uuid4()), "chapter_no": n, "content": f"{n}장 본문. " + "문장. " * 5000}
        for n in range(1, 21)
    ]
    draft = verdict(cs[:2])
    draft["tracks"][0]["evidence"] = [{"source_id": "c1p1"}]
    draft["findings"][0]["evidence"] = [{"source_id": "c1p1"}, {"source_id": "c2p1"}]
    final = copy.deepcopy(draft)
    final.update(findings=[], summary="중간 규칙 변경")
    with patch("app.ai.narrative_review.call_llm", side_effect=[draft, final]) as llm:
        result = run({"chapters": cs, "saved_rules": []})
    assert result["data"]["findings"] == [] and llm.call_count == 2
    indexed, refs = indexed_source(cs)
    assert ["".join(p["text"] for p in c["passages"]) for c in indexed] == [
        c["content"] for c in cs
    ]
    # Every original passage is included in BOTH full-context model passes, without truncation.
    for call in llm.call_args_list:
        assert all(
            json.dumps(p["text"], ensure_ascii=False) in call.args[0]
            for c in indexed
            for p in c["passages"]
        )
    assert result["data"]["tracks"][0]["evidence"][0]["quote"] == refs["c1p1"]["quote"]


def test_source_citations_are_resolved_from_original_and_sorted():
    cs = [
        {"chapter_id": str(uuid4()), "chapter_no": n, "content": f"  {n}장 👩‍🚀 실제 원문.\n"}
        for n in (1, 120)
    ]
    data = verdict(cs)
    data["tracks"][0]["evidence"] = [{"source_id": "c1p1"}]
    data["findings"][0]["evidence"] = [{"source_id": "c2p1"}, {"source_id": "c1p1"}]
    _, refs = indexed_source(cs)
    result = resolve_sources(SourceReview.model_validate(data), refs)
    validate_evidence(result, cs)
    assert [e.chapter_no for e in result.findings[0].evidence] == [1, 120]
    assert result.findings[0].evidence[1].quote == cs[1]["content"]
    data["findings"][0]["evidence"][0]["source_id"] = "invented"
    with pytest.raises(ValueError):
        resolve_sources(SourceReview.model_validate(data), refs)


@pytest.mark.parametrize("bad", ["quote", "chapter", "number", "duplicate", "payoff"])
def test_evidence_cannot_be_invented_or_misattributed(bad: str):
    cs = [
        {"chapter_id": str(uuid4()), "chapter_no": n, "content": f"{n}장의 실제 문장"}
        for n in (1, 100)
    ]
    data = verdict(cs)
    if bad == "quote":
        data["findings"][0]["evidence"][0]["quote"] = "없는 문장"
    if bad == "chapter":
        data["findings"][0]["evidence"][0]["chapter_id"] = str(uuid4())
    if bad == "number":
        data["findings"][0]["evidence"][0]["chapter_no"] = 2
    if bad == "duplicate":
        data["findings"][0]["evidence"][1] = data["findings"][0]["evidence"][0]
    if bad == "payoff":
        data["tracks"][0]["status"] = "paid_off"
    with pytest.raises(ValueError):
        validate_evidence(Review.model_validate(data), cs)
