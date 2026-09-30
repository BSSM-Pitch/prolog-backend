"""AIQ — 질문 스레드 · 답변 잡(qa_answer) · 폴링 · 재시도 · 선택 구간."""

from typing import Any
from uuid import UUID

from httpx import AsyncClient

from app.db.session import SessionFactory
from app.insight.aiq.service import handle, locate
from tests.conftest import FakeAI, code, signup

BODY = "1장. 비가 그쳤다. 윤서는 규칙 17을 처음 읽었다.\n2장. 윤서는 규칙을 알고 문을 열었다."


def answer(text: str) -> dict[str, Any]:
    return {"data": {"content": text}, "meta": {}}


def failure(code_: str) -> dict[str, Any]:
    return {"error": {"code": code_, "message": "응답 시간이 너무 길어졌습니다", "details": {}}}


async def _manuscript(client: AsyncClient, email: str, content: str = BODY) -> dict[str, Any]:
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
    return {
        "h": h,
        "ms": f"/projects/{pid}/manuscripts/{mid}",
        "base": f"/projects/{pid}/manuscripts/{mid}/qa-threads",
    }


async def _ask(client: AsyncClient, m: dict[str, Any], **body: Any) -> dict[str, Any]:
    res = await client.post(
        m["base"], json={"question": "윤서는 규칙 17을 언제 알았나요?"} | body, headers=m["h"]
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


def _job_of(thread: dict[str, Any]) -> UUID:
    """pending assistant 메시지의 잡 — 메시지 id 로 잡을 찾는다."""
    return UUID(thread["messages"][-1]["message_id"])


async def _run(message_id: UUID, ai: FakeAI) -> str:
    from sqlalchemy import select

    from app.insight.aiq.models import QaMessage

    async with SessionFactory() as s:
        job_id = (
            await s.execute(select(QaMessage.job_id).where(QaMessage.id == message_id))
        ).scalar_one()
    return await handle(SessionFactory, job_id, ai)


async def test_ask_answer_and_follow_up(client: AsyncClient) -> None:
    m = await _manuscript(client, "aq1@example.com")
    h = m["h"]
    created = await _ask(client, m)
    thread = created["thread"]
    assert (thread["scope"], thread["selection_range"], thread["title"]) == (
        "whole",
        None,
        "윤서는 규칙 17을 언제 알았나요?",
    )
    assert [(x["role"], x["status"], x["content"]) for x in created["messages"]] == [
        ("user", "completed", "윤서는 규칙 17을 언제 알았나요?"),
        ("assistant", "pending", None),
    ]
    ai = FakeAI(answer("1장에서 처음 읽습니다."))
    assert await _run(_job_of(created), ai) == "completed"
    # 전체 원고는 패키지의 project 다
    assert ai.calls == [("run_aiq", ("윤서는 규칙 17을 언제 알았나요?", BODY, "project", None, []))]

    tid, mid = thread["thread_id"], created["messages"][1]["message_id"]
    polled = (await client.get(f"{m['base']}/{tid}/messages/{mid}", headers=h)).json()["data"]
    assert (polled["status"], polled["content"], polled["error"]) == (
        "completed",
        "1장에서 처음 읽습니다.",
        None,
    )

    res = await client.post(
        f"{m['base']}/{tid}/messages", json={"content": "2장에서는요?"}, headers=h
    )
    assert res.status_code == 201
    followed = res.json()["data"]
    ai2 = FakeAI(answer("이미 알고 있습니다."))
    await _run(UUID(followed[1]["message_id"]), ai2)
    # 앞선 대화(답이 있는 것)가 함께 간다. 지금 질문은 messages 에 넣지 않는다
    assert ai2.calls[0][1][0] == "2장에서는요?"
    assert ai2.calls[0][1][4] == [
        {"role": "user", "content": "윤서는 규칙 17을 언제 알았나요?"},
        {"role": "assistant", "content": "1장에서 처음 읽습니다."},
    ]

    detail = (await client.get(f"{m['base']}/{tid}", headers=h)).json()["data"]
    assert [x["content"] for x in detail["messages"]] == [
        "윤서는 규칙 17을 언제 알았나요?",
        "1장에서 처음 읽습니다.",
        "2장에서는요?",
        "이미 알고 있습니다.",
    ]
    listed = (await client.get(m["base"], headers=h)).json()["data"]
    assert listed[0]["last_message"]["content"] == "이미 알고 있습니다."


async def test_selection_range_and_relocation(client: AsyncClient) -> None:
    m = await _manuscript(client, "aq2@example.com")
    h = m["h"]
    bad = await client.post(
        m["base"],
        json={"question": "q", "scope": "selection", "selection_range": {"start": 0, "end": 9999}},
        headers=h,
    )
    assert code(bad) == "INVALID_SELECTION_RANGE"
    missing = await client.post(m["base"], json={"question": "q", "scope": "selection"}, headers=h)
    assert code(missing) == "INVALID_SELECTION_RANGE"

    start = BODY.index("윤서는 규칙 17을")
    sel = {"start": start, "end": start + len("윤서는 규칙 17을 처음 읽었다.")}
    created = await _ask(client, m, scope="selection", selection_range=sel)
    assert created["thread"]["selected_text"] == "윤서는 규칙 17을 처음 읽었다."

    # 원고 앞에 문장이 붙어 오프셋이 밀렸다 — 선택한 문장을 다시 찾는다
    moved = "프롤로그.\n" + BODY
    await client.patch(m["ms"], json={"content": moved}, headers=h)
    ai = FakeAI(answer("답"))
    await _run(_job_of(created), ai)
    got = ai.calls[0][1]
    assert got[2] == "selection"
    assert moved[got[3]["start"] : got[3]["end"]] == "윤서는 규칙 17을 처음 읽었다."

    # 선택한 문장이 사라지면 다른 문장에 답하지 않는다
    again = await client.post(
        f"{m['base']}/{created['thread']['thread_id']}/messages",
        json={"content": "그다음은?"},
        headers=h,
    )
    await client.patch(m["ms"], json={"content": "완전히 다른 원고"}, headers=h)
    silent = FakeAI(answer("답"))
    assert await _run(UUID(again.json()["data"][1]["message_id"]), silent) == "failed"
    assert silent.calls == []
    failed = again.json()["data"][1]
    polled = await client.get(
        f"{m['base']}/{created['thread']['thread_id']}/messages/{failed['message_id']}", headers=h
    )
    assert polled.json()["data"]["error"]["code"] == "INVALID_SELECTION_RANGE"


def test_locate() -> None:
    assert locate("가나다라", 1, 3, "나다") == (1, 3)
    assert locate("xx가나다라", 1, 3, "나다") == (3, 5)
    assert locate("나다 … 나다", 5, 7, "나다") == (5, 7)
    assert locate("가라", 1, 3, "나다") is None


async def test_timeout_until_failed_then_retry(client: AsyncClient) -> None:
    m = await _manuscript(client, "aq3@example.com")
    h = m["h"]
    created = await _ask(client, m)
    tid, mid = created["thread"]["thread_id"], created["messages"][1]["message_id"]
    poll = f"{m['base']}/{tid}/messages/{mid}"
    outcomes = [await _run(UUID(mid), FakeAI(failure("AI_RESPONSE_TIMEOUT"))) for _ in range(3)]
    assert outcomes == ["retried", "retried", "exhausted"]
    polled = (await client.get(poll, headers=h)).json()["data"]
    assert (polled["status"], polled["error"]["code"]) == ("failed", "AI_RESPONSE_TIMEOUT")

    res = await client.post(f"{poll}/retry", headers=h)
    assert res.status_code == 202 and res.json()["data"]["status"] == "pending"
    assert await _run(UUID(mid), FakeAI(answer("다시 만든 답"))) == "completed"
    assert code(await client.post(f"{poll}/retry", headers=h)) == "INVALID_STATUS_TRANSITION"
    user_msg = created["messages"][0]["message_id"]
    res = await client.post(f"{m['base']}/{tid}/messages/{user_msg}/retry", headers=h)
    assert code(res) == "INVALID_STATUS_TRANSITION"


async def test_failed_is_final_for_ai_failed(client: AsyncClient) -> None:
    m = await _manuscript(client, "aq4@example.com")
    created = await _ask(client, m)
    assert await _run(_job_of(created), FakeAI(failure("AI_RESPONSE_FAILED"))) == "failed"


async def test_delete_thread_and_orphan_job(client: AsyncClient) -> None:
    m = await _manuscript(client, "aq5@example.com")
    h = m["h"]
    created = await _ask(client, m)
    tid = created["thread"]["thread_id"]
    from sqlalchemy import select

    from app.insight.aiq.models import QaMessage

    async with SessionFactory() as s:
        job_id = (
            await s.execute(select(QaMessage.job_id).where(QaMessage.id == _job_of(created)))
        ).scalar_one()
    assert (await client.delete(f"{m['base']}/{tid}", headers=h)).status_code == 204
    assert code(await client.get(f"{m['base']}/{tid}", headers=h)) == "QA_THREAD_NOT_FOUND"
    ai = FakeAI(answer("답"))
    assert await handle(SessionFactory, job_id, ai) == "failed"  # 쓸 메시지가 없다
    assert ai.calls == []


async def test_empty_manuscript_and_missing_message(client: AsyncClient) -> None:
    m = await _manuscript(client, "aq6@example.com", content="")
    assert (
        code(await client.post(m["base"], json={"question": "q"}, headers=m["h"]))
        == "INVALID_INPUT"
    )
    full = await _manuscript(client, "aq7@example.com")
    tid = (await _ask(client, full))["thread"]["thread_id"]
    res = await client.get(f"{full['base']}/{tid}/messages/{UUID(int=3)}", headers=full["h"])
    assert code(res) == "QA_MESSAGE_NOT_FOUND"


async def test_zombie_answer_ends_failed(client: AsyncClient) -> None:
    """스위퍼가 좀비로 끝낸 답변은 failed — 화면 36 의 "다시 시도" 가 보인다."""
    from datetime import timedelta

    from sqlalchemy import func, select, update

    from app.insight.aiq.models import QaMessage
    from app.jobs.models import Job
    from worker import sweeper

    m = await _manuscript(client, "aq8@example.com")
    created = await _ask(client, m)
    mid = _job_of(created)
    async with SessionFactory() as s:
        job_id = (await s.execute(select(QaMessage.job_id).where(QaMessage.id == mid))).scalar_one()
        await s.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(status="running", attempt=3, started_at=func.now() - timedelta(hours=1))
        )
        await s.commit()
        assert await sweeper.reap_zombies(s) == 1
        await s.commit()
        status = (await s.execute(select(QaMessage.status).where(QaMessage.id == mid))).scalar_one()
    assert status == "failed"
