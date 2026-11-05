"""Success must never reach the client before its database transaction commits."""

from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.session import Session


async def _request(*, commit_fails: bool = False) -> tuple[int, list[str]]:
    events = []

    class Transaction:
        async def commit(self):
            events.append("commit")
            if commit_fails:
                raise RuntimeError("database commit rejected")

        async def rollback(self):
            events.append("rollback")

    @asynccontextmanager
    async def factory():
        yield Transaction()

    mini = FastAPI()

    @mini.post("/write")
    async def write(session: Session):
        events.append("write")
        return {"created": True}

    async def observed(scope, receive, send):
        async def observe_send(message):
            if message["type"] == "http.response.start":
                events.append(f"response:{message['status']}")
            await send(message)

        await mini(scope, receive, observe_send)

    with patch("app.db.session.SessionFactory", factory):
        async with AsyncClient(
            transport=ASGITransport(app=observed, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post("/write")
    return response.status_code, events


async def test_commit_finishes_before_success_response_starts() -> None:
    status, events = await _request()
    assert status == 200
    assert events == ["write", "commit", "response:200"]


async def test_failed_commit_never_reports_success() -> None:
    status, events = await _request(commit_fails=True)
    assert status == 500
    assert events == ["write", "commit", "rollback", "response:500"]
