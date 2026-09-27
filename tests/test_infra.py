"""Phase 1 인프라 — Outbox 릴레이 · 잡 최소 코어."""

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.events.models import OutboxEvent
from app.events.relay import relay_once
from app.jobs import service as jobs
from tests.conftest import signup


class FakeQueue:
    """큐 포트의 테스트 구현. 테스트가 elasticmq 를 띄우지 않아도 되게 한다."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send(self, queue_name: str, body: dict[str, Any]) -> None:
        self.sent.append((queue_name, body))


async def test_relay_publishes_each_event_once(db: AsyncSession) -> None:
    event = OutboxEvent(
        aggregate_type="team",
        aggregate_id=uuid4(),
        event_type="team.invited",
        payload={"team_id": "t1"},
    )
    db.add(event)
    await db.commit()

    queue = FakeQueue()
    assert await relay_once(db, queue, batch=10) == 1

    name, body = queue.sent[0]
    assert name == "io"
    assert body["event_type"] == "team.invited"
    assert body["payload"] == {"team_id": "t1"}
    assert body["event_id"] == str(event.id)

    await db.refresh(event)
    assert event.published_at is not None

    # 표시된 이벤트는 다시 가지 않는다.
    assert await relay_once(db, queue, batch=10) == 0
    assert len(queue.sent) == 1


async def test_relay_is_a_noop_when_outbox_is_empty(db: AsyncSession) -> None:
    queue = FakeQueue()
    assert await relay_once(db, queue, batch=10) == 0
    assert queue.sent == []


async def test_job_core_lifecycle(client: AsyncClient, db: AsyncSession) -> None:
    user = await signup(client, "job@example.com")
    project = (
        await client.post(
            "/projects", json={"title": "J", "owner_type": "personal"}, headers=user["headers"]
        )
    ).json()["data"]

    job = await jobs.create(
        db,
        project_id=UUID(project["project_id"]),
        job_type="manuscript_extraction",
        target_type="manuscript",
        queue="io",
    )
    await db.commit()
    assert job.status == "queued"
    # 끝나지 않은 잡에는 폴링 힌트가 붙는다 (ROADMAP Phase 1).
    assert jobs.meta(job) == {"retry_after_ms": 1000}

    await jobs.transition(db, job, "running")
    assert job.status == "running"
    await jobs.transition(db, job, "completed", result={"chars": 12})
    await db.commit()
    assert job.result == {"chars": 12}
    assert job.finished_at is not None
    # 끝난 잡에는 붙지 않는다.
    assert jobs.meta(job) == {}

    assert await jobs.get(db, job.id) is job


async def test_job_rejects_illegal_transition(client: AsyncClient, db: AsyncSession) -> None:
    user = await signup(client, "job2@example.com")
    project = (
        await client.post(
            "/projects", json={"title": "J", "owner_type": "personal"}, headers=user["headers"]
        )
    ).json()["data"]
    job = await jobs.create(
        db,
        project_id=UUID(project["project_id"]),
        job_type="manuscript_extraction",
        target_type="manuscript",
        queue="io",
    )
    await db.commit()

    # queued 에서 바로 completed 로 갈 수 없다.
    with pytest.raises(errors.InvalidStatusTransition):
        await jobs.transition(db, job, "completed", result={})

    # 재시도(failed → queued)는 Phase 2 다. 최소 코어는 열어 두지 않는다.
    await jobs.transition(db, job, "running")
    await jobs.transition(db, job, "failed", error={"code": "X"})
    with pytest.raises(errors.InvalidStatusTransition):
        await jobs.transition(db, job, "queued")
