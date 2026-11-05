"""SCDS — 사건 저장 · 룰 검출(동기, 실제 패키지) · AI 분석 잡 · 충돌 처리 · 억제."""

from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory
from app.events.models import OutboxEvent
from app.insight.scds.service import handle
from tests.conftest import FakeAI, code, signup

VIOLATION = "윤서는 비 오는 중에 열림 표시를 무시하고 붉은 문을 밀었다."


async def _world(client: AsyncClient, email: str, *, rule: bool = True) -> dict[str, Any]:
    user = await signup(client, email)
    h = user["headers"]
    pid = (
        await client.post("/projects", json={"title": "P", "owner_type": "personal"}, headers=h)
    ).json()["data"]["project_id"]
    ms = (
        await client.post(f"/projects/{pid}/manuscripts", json={"title": "원고"}, headers=h)
    ).json()["data"]["manuscript_id"]
    chapter = (
        await client.post(
            f"/projects/{pid}/chapters", json={"manuscript_id": ms, "chapter_no": 27}, headers=h
        )
    ).json()["data"]["chapter_id"]
    draft = (
        await client.post(
            f"/projects/{pid}/character-drafts", json={"character_name": "윤서"}, headers=h
        )
    ).json()["data"]["draft_id"]
    await client.post(
        f"/projects/{pid}/character-drafts/{draft}/items",
        json={"field": "core_values", "value": "약속 중시"},
        headers=h,
    )
    char = (
        await client.post(f"/projects/{pid}/character-drafts/{draft}/confirm", json={}, headers=h)
    ).json()["data"]["character_id"]
    rule_id = None
    if rule:
        rule_id = (
            await client.post(
                f"/projects/{pid}/world-rules",
                json={
                    "title": "붉은 문",
                    "description": "붉은 문은 비가 그친 뒤에만 열린다",
                    "violation_keywords": ["비 오는 중에 열림"],
                },
                headers=h,
            )
        ).json()["data"]["rule_id"]
    return {"h": h, "pid": pid, "chapter": chapter, "char": char, "rule": rule_id}


async def _event(
    client: AsyncClient, w: dict[str, Any], content: str = VIOLATION
) -> dict[str, Any]:
    res = await client.post(
        f"/projects/{w['pid']}/chapters/{w['chapter']}/events",
        json={"character_ids": [w["char"]], "content": content},
        headers=w["h"],
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


def advice_for(w: dict[str, Any]) -> dict[str, Any]:
    assessment = {
        "rule_id": w["rule"],
        "rule_title": "붉은 문",
        "rule_description": "붉은 문은 비가 그친 뒤에만 열린다",
        "verdict": "violates",
        "evidence": VIOLATION,
        "severity": "high",
        "reason": "비가 그친 뒤로 옮기는 것을 고려해 보세요.",
    }
    return {"data": {"assessments": [assessment]}, "meta": {}}


async def test_rule_detection_then_ai_advice(client: AsyncClient, db: AsyncSession) -> None:
    w = await _world(client, "sc1@example.com")
    h, pid = w["h"], w["pid"]
    created = await _event(client, w)
    assert created["event"]["chapter"] == 27 and created["event"]["content"] == VIOLATION
    check = created["conflict_check"]
    assert check["status"] == "queued"
    assert check["rule_result"]["candidates"] == [
        {
            "rule_id": w["rule"],
            "character_id": w["char"],
            "conflict_target": "붉은 문은 비가 그친 뒤에만 열린다",
            "matched_keyword": "비 오는 중에 열림",
        }
    ]
    queued = (
        await db.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(check["check_id"]))
        )
    ).scalar_one()
    assert queued.payload["queue"] == "ai"

    ai = FakeAI(advice_for(w))
    assert await handle(SessionFactory, UUID(check["check_id"]), ai) == "completed"
    event_arg, rule_arg, chars_arg = ai.calls[0][1]
    assert event_arg == {"character_ids": [w["char"]], "content": VIOLATION}
    assert rule_arg[0]["rule_id"] == w["rule"]
    assert ai.calls[0][0] == "run_rule_review"
    assert chars_arg[0]["core_values"] == ["약속 중시"]

    got = (
        await client.get(f"/projects/{pid}/conflict-checks/{check['check_id']}", headers=h)
    ).json()
    assert got["data"]["status"] == "completed" and got["error"] is None
    [cid] = got["data"]["conflict_ids"]
    conflict = (await client.get(f"/projects/{pid}/conflicts/{cid}", headers=h)).json()["data"]
    assert (conflict["detected_by"], conflict["severity"], conflict["chapter"]) == (
        "ai",
        "high",
        27,
    )
    assert (conflict["status"], conflict["input_event"], conflict["rule_id"]) == (
        "pending",
        VIOLATION,
        w["rule"],
    )
    pending = (await client.get(f"/projects/{pid}/conflicts?status=pending", headers=h)).json()[
        "data"
    ]
    assert [c["conflict_id"] for c in pending] == [cid]


async def test_no_rules_skips_but_absent_keyword_still_gets_ai_review(client: AsyncClient) -> None:
    w = await _world(client, "sc2@example.com", rule=False)
    none = await _event(client, w)
    # 캐릭터에 핵심 가치가 있어 참조 데이터는 있다 — 걸린 규칙이 없을 뿐
    assert none["conflict_check"]["status"] == "skipped"
    assert none["conflict_check"]["rule_result"]["skipped"] is False
    w2 = await _world(client, "sc3@example.com")
    miss = await _event(client, w2, content="윤서는 비가 그친 뒤 문을 열었다.")
    assert miss["conflict_check"]["status"] == "queued"
    assert miss["conflict_check"]["rule_result"]["has_candidate"] is False


async def test_resolve_and_ignored_is_suppressed(client: AsyncClient) -> None:
    w = await _world(client, "sc4@example.com")
    h, pid = w["h"], w["pid"]
    check = (await _event(client, w))["conflict_check"]
    await handle(SessionFactory, UUID(check["check_id"]), FakeAI(advice_for(w)))
    cid = (await client.get(f"/projects/{pid}/conflicts", headers=h)).json()["data"][0][
        "conflict_id"
    ]
    path = f"/projects/{pid}/conflicts/{cid}"

    assert code(await client.patch(path, json={"action": "modified"}, headers=h)) == "INVALID_INPUT"
    res = await client.patch(path, json={"action": "ignored"}, headers=h)
    assert res.json()["data"]["status"] == "ignored" and res.json()["data"]["resolved_at"]
    assert code(await client.patch(path, json={"action": "accepted"}, headers=h)) == (
        "INVALID_STATUS_TRANSITION"
    )
    history = (await client.get(f"/projects/{pid}/conflicts/history", headers=h)).json()["data"]
    assert [(x["conflict_id"], x["status"]) for x in history] == [(cid, "ignored")]

    # 같은 캐릭터 · 규칙 · 사건 문장(공백·대소문자 무시)은 다시 후보가 되지 않는다
    again = await _event(client, w, content="  " + VIOLATION.replace(" ", "   ") + " ")
    assert again["conflict_check"]["status"] == "skipped"
    assert again["conflict_check"]["rule_result"]["suppressed_count"] == 1
    # 다른 문장은 억제되지 않는다
    other = await _event(client, w, content="재현은 비 오는 중에 열림을 확인하고 문을 열었다.")
    assert other["conflict_check"]["status"] == "queued"


async def test_ai_failure_keeps_only_unverified_keyword_clues_and_retry(
    client: AsyncClient,
) -> None:
    w = await _world(client, "sc5@example.com")
    h, pid = w["h"], w["pid"]
    check_id = (await _event(client, w))["conflict_check"]["check_id"]
    failed = {
        "error": {
            "code": "AI_ANALYSIS_FAILED",
            "message": "실패",
            "details": {"rule_result": {"candidates": []}},
        }
    }
    assert await handle(SessionFactory, UUID(check_id), FakeAI(failed)) == "failed"
    got = (await client.get(f"/projects/{pid}/conflict-checks/{check_id}", headers=h)).json()
    assert got["error"] == {"code": "AI_ANALYSIS_FAILED", "message": "실패", "details": {}}
    assert got["data"]["conflict_ids"] == []
    assert got["data"]["assessments"][0]["verdict"] == "undetermined"
    # A keyword match is retained separately; it never becomes a violation on AI failure.
    assert got["data"]["rule_result"]["has_candidate"]

    res = await client.post(f"/projects/{pid}/conflict-checks/{check_id}/retry", headers=h)
    assert res.status_code == 202 and res.json()["data"]["status"] == "queued"
    assert res.json()["data"]["conflict_ids"] == []  # 룰 후보로 남긴 충돌은 지웠다
    await handle(SessionFactory, UUID(check_id), FakeAI(advice_for(w)))
    done = (await client.get(f"/projects/{pid}/conflict-checks/{check_id}", headers=h)).json()[
        "data"
    ]
    assert done["status"] == "completed" and len(done["conflict_ids"]) == 1
    res = await client.post(f"/projects/{pid}/conflict-checks/{check_id}/retry", headers=h)
    assert code(res) == "INVALID_STATUS_TRANSITION"


async def test_unknown_chapter_or_character(client: AsyncClient) -> None:
    w = await _world(client, "sc6@example.com")
    h, pid = w["h"], w["pid"]
    res = await client.post(
        f"/projects/{pid}/chapters/{UUID(int=1)}/events",
        json={"character_ids": [w["char"]], "content": VIOLATION},
        headers=h,
    )
    assert code(res) == "CHAPTER_NOT_FOUND"
    res = await client.post(
        f"/projects/{pid}/chapters/{w['chapter']}/events",
        json={"character_ids": [str(UUID(int=2))], "content": VIOLATION},
        headers=h,
    )
    assert code(res) == "CHARACTER_NOT_FOUND"
    assert code(await client.get(f"/projects/{pid}/conflicts/{UUID(int=3)}", headers=h)) == (
        "CONFLICT_NOT_FOUND"
    )
    assert code(await client.get(f"/projects/{pid}/conflict-checks/{UUID(int=4)}", headers=h)) == (
        "CONFLICT_CHECK_NOT_FOUND"
    )


async def test_positive_keyword_is_not_a_violation_and_character_is_optional(
    client: AsyncClient,
) -> None:
    w = await _world(client, "sc-positive@example.com")
    await client.patch(
        f"/projects/{w['pid']}/world-rules/{w['rule']}",
        json={"description": "정확히 7시간 뒤의 메시지만 수신", "violation_keywords": ["7시간"]},
        headers=w["h"],
    )
    res = await client.post(
        f"/projects/{w['pid']}/chapters/{w['chapter']}/events",
        json={
            "character_ids": [],
            "content": (
                "현재 시각은 새벽 두 시. 발신 시각은 오늘 오전 아홉 시. "
                "정확히 7시간 뒤의 메시지였다."
            ),
        },
        headers=w["h"],
    )
    assert res.status_code == 201, res.text
    check = res.json()["data"]["conflict_check"]
    assert check["status"] == "queued"
    assessment = {
        "rule_id": w["rule"],
        "rule_title": "미래 로그",
        "rule_description": "정확히 7시간 뒤의 메시지만 수신",
        "verdict": "supports",
        "evidence": "정확히 7시간 뒤의 메시지였다.",
        "reason": "같은 날 02:00에서 09:00까지 7시간이므로 일치",
        "severity": None,
    }
    assert (
        await handle(
            SessionFactory, UUID(check["check_id"]), FakeAI({"data": {"assessments": [assessment]}})
        )
        == "completed"
    )
    result = (
        await client.get(
            f"/projects/{w['pid']}/conflict-checks/{check['check_id']}", headers=w["h"]
        )
    ).json()["data"]
    assert result["analysis_kind"] == "ai_rule_review"
    assert result["assessments"][0]["verdict"] == "supports"
    assert result["conflict_ids"] == []


async def test_changed_rule_cannot_publish_a_stale_violation(client: AsyncClient) -> None:
    w = await _world(client, "sc-rule-changed@example.com")
    check = (await _event(client, w))["conflict_check"]
    await client.patch(
        f"/projects/{w['pid']}/world-rules/{w['rule']}",
        json={"description": "붉은 문은 언제든 열 수 있다"},
        headers=w["h"],
    )
    assert (
        await handle(SessionFactory, UUID(check["check_id"]), FakeAI(advice_for(w))) == "completed"
    )
    got = (
        await client.get(
            f"/projects/{w['pid']}/conflict-checks/{check['check_id']}", headers=w["h"]
        )
    ).json()["data"]
    assert got["conflict_ids"] == []
    assert got["assessments"][0]["verdict"] == "undetermined"
