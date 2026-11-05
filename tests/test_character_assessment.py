"""Rubric scores must stay traceable, scoped, and separate from extraction frequency."""

import json
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.ai import character_assessment as model
from app.db.session import SessionFactory
from app.insight.character_memory import assessment, service
from app.insight.character_memory.projection import dossier
from app.insight.character_memory.schemas import CharacterAssessmentResult
from app.jobs import service as jobs
from tests.conftest import FakeAI, code, signup
from tests.test_character_memory import fixture_memory, start
from tests.test_ssm import _manuscript


def source(cutoff: int | None = None) -> dict[str, Any]:
    return assessment.evaluation_source(dossier(fixture_memory(), "a", {}, cutoff))


def draft(payload: dict[str, Any]) -> dict[str, Any]:
    records = list({r["scene_id"]: r for r in payload["records"]}.values())[:3]
    return {
        "axes": [
            {
                "axis_id": rubric["id"],
                "verdict": "테스트용 평가",
                "conditions": "묘사된 상황에서",
                "limitations": "다른 상황은 판단할 수 없음",
                "ratings": [
                    {
                        "scene_id": r["scene_id"],
                        "level": level,
                        "observation_ids": [r["observation_id"]],
                        "explanation": "이 원문의 행동을 기준으로 채점",
                    }
                    for r, level in zip(records, [2, 4, 5], strict=False)
                ],
                "counterevidence_ids": [records[0]["observation_id"]] if records else [],
            }
            for rubric in model.RUBRIC
        ]
    }


def test_score_is_scene_median_with_abstention_not_frequency() -> None:
    payload = source()
    result = assessment.calculate(draft(payload), payload)
    assert {axis["score"] for axis in result["axes"]} == {4}
    assert all(a["observed_range"] == [2, 5] and a["rated_scenes"] == 3 for a in result["axes"])
    # Repeated extracted records cannot increase a scene's influence.
    repeated = deepcopy(payload)
    repeated["records"] *= 10
    assert assessment.calculate(draft(payload), repeated) == result
    insufficient = assessment.calculate(draft(source(2)), source(2))
    assert all(a["score"] is None for a in insufficient["axes"])
    assert all(a["status"] == "insufficient_evidence" for a in insufficient["axes"])
    CharacterAssessmentResult.model_validate({**result, "meta": {"review_passes": 2}})


@pytest.mark.parametrize(
    "invalid",
    [
        "unknown",
        "wrong_scene",
        "duplicate_scene",
        "duplicate_evidence",
        "claim",
        "future",
        "uncertain",
        "duplicate_axis",
        "counter",
        "duplicate_counter",
    ],
)
def test_withholds_unverifiable_axes_without_losing_verified_axes(invalid: str) -> None:
    payload = source(3)
    raw = draft(payload)
    axis = raw["axes"][0]
    rating = axis["ratings"][0]
    if invalid in ("unknown", "future", "uncertain"):
        rating["observation_ids"] = [
            {"unknown": "missing", "future": "o6", "uncertain": "uncertain"}[invalid]
        ]
    elif invalid == "wrong_scene":
        rating["scene_id"] = "s3"
    elif invalid == "duplicate_scene":
        axis["ratings"].append(deepcopy(rating))
    elif invalid == "duplicate_evidence":
        rating["observation_ids"] *= 2
    elif invalid == "claim":
        payload["records"][0]["presentation"] = "character_claim"
    elif invalid == "duplicate_axis":
        raw["axes"][1] = deepcopy(axis)
    elif invalid == "counter":
        axis["counterevidence_ids"] = ["missing"]
    elif invalid == "duplicate_counter":
        axis["counterevidence_ids"] *= 2
    with pytest.raises(ValueError):
        model.validate_references(model.AssessmentDraft.model_validate(raw), payload)
    if invalid == "duplicate_axis":
        with pytest.raises(ValueError):
            assessment.calculate(raw, payload)
    else:
        calculated = assessment.calculate(raw, payload)
        withheld = calculated["axes"][0]
        assert withheld["status"] == "unverified_evidence" and withheld["score"] is None
        assert withheld["ratings"] == withheld["counterevidence_ids"] == []
        assert raw["axes"][0]["verdict"] != withheld["verdict"]
        if invalid != "claim":
            assert calculated["axes"][1]["score"] == 4


def test_sampling_bounds_balances_scenes_and_prefers_counterevidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = dossier(fixture_memory(), "a", {}, None)
    # Flood the first scene; it must not crowd the later scenes out.
    first = data["observations"][0]
    data["observations"] = [dict(first, observation_id=f"copy-{i}") for i in range(30)] + data[
        "observations"
    ]
    data["patterns"][0]["counterexample_observation_ids"].append("copy-29")
    monkeypatch.setattr(assessment, "MAX_RECORDS", 6)
    sampled = assessment.evaluation_source(data)
    assert sampled["coverage"]["reviewed_scenes"] == 6
    assert len(sampled["records"]) == 6 and not sampled["coverage"]["exhaustive"]
    assert sampled["records"][0]["observation_id"] == "copy-29"
    monkeypatch.setattr(assessment, "MAX_RECORDS", 3)
    long_book = assessment.evaluation_source(data)
    assert len(long_book["records"]) == 3
    assert long_book["records"][0]["order"] == 1
    assert long_book["records"][-1]["order"] == 6
    assert long_book["coverage"]["available_scenes"] == 6
    assert source(2)["coverage"]["available_scenes"] == 2
    assert all(r["order"] <= 2 for r in source(2)["records"])
    monkeypatch.setattr(assessment, "MAX_SOURCE_CHARS", 1000)
    limited = assessment.evaluation_source(data)
    assert sum(len(json.dumps(r, ensure_ascii=False)) for r in limited["records"]) <= 1000


def test_two_pass_review_and_safe_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import prolog_ai.core.llm as llm

    monkeypatch.delenv("USE_FAKE_LLM", raising=False)
    payload = source()
    first, second = draft(payload), draft(payload)
    second["axes"][0]["verdict"] = "반례를 반영한 평가"
    prompts = []
    results = [first, second]

    def mock_call(prompt: str, **kwargs: Any) -> dict[str, Any]:
        prompts.append(prompt)
        assert kwargs["schema"] is model.AssessmentDraft
        return results.pop(0)

    monkeypatch.setattr(llm, "call_llm", mock_call)
    result = model.run(payload)
    assert result["data"] == second and result["meta"]["review_passes"] == 2
    assert "draft_to_review" not in prompts[0] and "draft_to_review" in prompts[1]

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private provider source and secret")

    monkeypatch.setattr(llm, "call_llm", broken)
    assert model.run(payload) == {"error": {"code": "AI_ANALYSIS_FAILED"}}
    monkeypatch.setenv("USE_FAKE_LLM", "1")
    assert model.run(payload) == {"error": {"code": "ASSESSMENT_REAL_MODEL_REQUIRED"}}


def test_insufficient_source_skips_model_and_bad_result_cannot_be_saved() -> None:
    ai = FakeAI()
    result = assessment.call(ai, source(2))
    assert not ai.calls and result["meta"]["review_passes"] == 0
    assert all(a["score"] is None for a in result["data"]["axes"])
    invalid = draft(source())
    invalid["axes"][0]["ratings"][0]["observation_ids"] = ["hallucinated"]
    withheld = assessment.call(FakeAI({"data": invalid}), source())["data"]["axes"]
    assert withheld[0]["score"] is None and withheld[0]["status"] == "unverified_evidence"
    assert withheld[1]["score"] == 4
    assert assessment.call(
        FakeAI({"error": {"code": "AI_ANALYSIS_FAILED", "message": "secret"}}), source()
    ) == {"error": {"code": "AI_ANALYSIS_FAILED"}}


def test_alias_roundtrip_and_review_gets_failed_reference_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prolog_ai.core.llm as llm

    monkeypatch.delenv("USE_FAKE_LLM", raising=False)
    payload = source()
    for r in payload["records"]:
        r["scene_id"] = "original-scene-" + r["scene_id"]
        r["observation_id"] = "original-observation-" + r["observation_id"]
    short, _, _ = model.aliased_source(payload)
    first, second = draft(short), draft(short)
    first["axes"][0]["ratings"][0]["scene_id"] = "s2"
    responses = [first, second]
    prompts = []

    def mock_call(prompt: str, **kwargs: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(llm, "call_llm", mock_call)
    result = model.run(payload)
    assert result["data"] == draft(payload)
    assert "original-observation" not in prompts[0]
    assert "agency, rating 1: evidence outside rated scene" in prompts[1]
    assert assessment.calculate(result["data"], payload)["axes"][0]["score"] == 4
    responses.extend([second, first, first])
    still_invalid = model.run(payload)
    assert (
        assessment.calculate(still_invalid["data"], payload)["axes"][0]["status"]
        == "unverified_evidence"
    )
    responses.extend([second, first, second])
    repaired = model.run(payload)
    assert repaired["data"] == draft(payload)
    assert repaired["meta"]["reference_repair_passes"] == 1
    assert len(prompts) == 8  # two normal passes, then two attempts with one bounded repair each


async def test_partial_verification_is_explicit_and_can_be_retried(client: AsyncClient) -> None:
    m = await ready(client)
    aid = UUID((await client.post(m["url"], headers=m["h"])).json()["data"]["assessment_id"])
    async with SessionFactory() as session:
        job = await jobs.get(session, aid)
        assert job
        payload = job.input["payload"]
    raw = draft(payload)
    raw["axes"][0]["ratings"][0]["observation_ids"] = ["unknown"]
    assert await assessment.handle(SessionFactory, aid, FakeAI({"data": raw})) == "completed"
    response = (await client.get(m["url"], headers=m["h"])).json()["data"]["job"]["result"]
    assert response["axes"][0]["status"] == "unverified_evidence"
    assert response["axes"][0]["score"] is None
    assert all(a["score"] == 4 for a in response["axes"][1:])
    newer = UUID((await client.post(m["url"], headers=m["h"])).json()["data"]["assessment_id"])
    assert newer != aid
    assert (
        await assessment.handle(SessionFactory, newer, FakeAI({"data": draft(payload)}))
        == "completed"
    )


def test_unknown_model_alias_cannot_collide_with_a_real_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prolog_ai.core.llm as llm

    monkeypatch.delenv("USE_FAKE_LLM", raising=False)
    payload = source()
    payload["records"][0]["observation_id"] = "__unresolved__"
    short, _, _ = model.aliased_source(payload)
    raw = draft(short)
    raw["axes"][0]["ratings"][0]["observation_ids"] = ["invented"]
    monkeypatch.setattr(llm, "call_llm", lambda *args, **kwargs: deepcopy(raw))
    response = model.run(payload)
    assert (
        assessment.calculate(response["data"], payload)["axes"][0]["status"]
        == "unverified_evidence"
    )


async def ready(client: AsyncClient) -> dict[str, Any]:
    m = await _manuscript(client, "assessment-owner@example.com")
    project, mid = m["base"].split("/manuscripts/")
    for i in range(1, 4):
        r = await client.post(
            f"{project}/chapters",
            headers=m["h"],
            json={"manuscript_id": mid, "chapter_no": i, "content": "그는 확인하고 결정했다."},
        )
        assert r.status_code == 201
    aid, memory = await start(client, m)
    for i, s in enumerate(memory["scenes"]):
        memory["observations"].append(
            {
                "observation_id": f"o{i}",
                "character_id": m["char"],
                "attribution": "certain",
                "kind": "action",
                "presentation": "depicted",
                "behavior_role": "choice",
                "summary": "확인 후 결정",
                "context": "문제 해결",
                "interlocutor_ids": [],
                "evidence": {
                    "scene_id": s["scene_id"],
                    "revision": s["revision"],
                    "start": 0,
                    "end": len(s["text"]),
                    "quote": s["text"],
                },
            }
        )
    assert await service.handle(SessionFactory, aid, FakeAI({"data": memory})) == "completed"
    return {**m, "url": f"{m['base']}/characters/{m['char']}/assessment", "project": project}


async def test_api_snapshot_cutoff_cache_worker_and_stale(client: AsyncClient) -> None:
    m = await ready(client)
    url, h = m["url"], m["h"]
    assert (await client.get(url, headers=h)).json()["data"]["job"] is None
    created = await client.post(url, headers=h)
    assert created.status_code == 202
    aid = UUID(created.json()["data"]["assessment_id"])
    assert (await client.post(url, headers=h)).json()["data"]["assessment_id"] == str(aid)
    async with SessionFactory() as session:
        job = await jobs.get(session, aid)
        assert job
        payload = job.input["payload"]
    assert (
        await assessment.handle(
            SessionFactory, aid, FakeAI({"data": draft(payload), "meta": {"review_passes": 2}})
        )
        == "completed"
    )
    assert await assessment.handle(SessionFactory, aid, FakeAI()) == "skipped"
    result = (await client.get(url, headers=h)).json()["data"]["job"]
    assert result["result"]["axes"][0]["score"] == 4
    assert (await client.post(url, headers=h)).json()["data"]["assessment_id"] == str(aid)
    assert (await client.get(url + "?through_order=2", headers=h)).json()["data"]["job"] is None
    cutoff = await client.post(url + "?through_order=2", headers=h)
    past_id = UUID(cutoff.json()["data"]["assessment_id"])
    assert past_id != aid
    assert await assessment.handle(SessionFactory, past_id, FakeAI()) == "completed"
    past = (await client.get(url + "?through_order=2", headers=h)).json()["data"]["job"]["result"]
    assert all(a["score"] is None for a in past["axes"])
    assert past["coverage"]["available_scenes"] == 2
    assert (await client.get(url + "?through_order=-1", headers=h)).status_code == 400
    scene_id = payload["records"][0]["scene_id"]
    await client.patch(
        f"{m['project']}/chapters/{scene_id}", headers=h, json={"content": "변경된 본문"}
    )
    assert (await client.get(url, headers=h)).json()["data"]["stale"]
    assert code(await client.post(url, headers=h)) == "INVALID_INPUT"


async def test_assessment_permissions_and_failed_retry(client: AsyncClient) -> None:
    m = await ready(client)
    other = await _manuscript(client, "assessment-outsider@example.com")
    url, h = m["url"], m["h"]
    assert (await client.get(url, headers=other["h"])).status_code == 403
    assert (await client.post(url, headers=other["h"])).status_code == 403
    assert (await client.get(url.replace(m["char"], other["char"]), headers=h)).status_code == 404
    viewer = await signup(client, "assessment-viewer@example.com")
    invite = (
        await client.post(
            f"{m['project']}/invitations",
            json={"invited_email": viewer["email"], "role": "viewer"},
            headers=h,
        )
    ).json()["data"]
    await client.post(
        f"{m['project']}/invitations/{invite['invitation_id']}/accept",
        json={"token": invite["token"]},
        headers=viewer["headers"],
    )
    assert (await client.get(url, headers=viewer["headers"])).status_code == 200
    assert (await client.post(url, headers=viewer["headers"])).status_code == 403
    aid = UUID((await client.post(url, headers=h)).json()["data"]["assessment_id"])
    assert (
        await assessment.handle(
            SessionFactory,
            aid,
            FakeAI({"error": {"code": "AI_ANALYSIS_FAILED", "message": "private secret"}}),
        )
        == "failed"
    )
    got = (await client.get(url, headers=h)).json()["data"]["job"]
    assert got["status"] == "failed" and got["result"] is None
    assert "private" not in json.dumps(got)
    assert (await client.post(url, headers=h)).json()["data"]["assessment_id"] != str(aid)
