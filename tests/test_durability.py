"""Phase 2a — 잡·워커 내구성.

`scripts/verify/durability.py` 의 두 재현(poison · dup)을 실물 없이 같은 시나리오로 다시 돈다.
그 외에 선점 경합, 좀비 회수와 늦게 돌아온 워커의 경합, 재시도·시도 소진, 업로드 콜백 유실.
"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts import service as manuscripts
from app.content.manuscripts.extraction import FileExtractor
from app.content.manuscripts.job_handler import STORAGE_FAILED, handle
from app.content.manuscripts.models import Manuscript
from app.core.config import settings
from app.db.session import SessionFactory
from app.events import consumer
from app.events.models import OutboxEvent, ProcessedEvent
from app.events.queue import Message
from app.events.relay import relay_once
from app.jobs import service as jobs
from app.jobs.models import Job
from app.platform_.notifications.models import Notification
from tests.conftest import FakeQueue, FakeStorage, StopConsuming, signup
from tests.test_extraction import _queued_extraction
from tests.test_teams import create_team
from worker import notifier, sweeper


async def _invite_event(client: AsyncClient, db: AsyncSession) -> tuple[dict, dict]:
    """초대 → 릴레이. (초대받은 사람, 큐로 나간 메시지 본문)."""
    owner = await signup(client, "dur-owner@example.com")
    guest = await signup(client, "dur-guest@example.com")
    team_id = await create_team(client, owner["headers"])
    res = await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": guest["email"]},
        headers=owner["headers"],
    )
    assert res.status_code == 201
    queue = FakeQueue()
    assert await relay_once(db, queue, batch=10) == 1
    return guest, queue.sent[0][1]


async def _notifications_of(db: AsyncSession, user_id: str) -> int:
    stmt = (
        select(func.count()).select_from(Notification).where(Notification.user_id == UUID(user_id))
    )
    return (await db.execute(stmt)).scalar_one()


# --- durability.py dup ------------------------------------------------------------


async def test_same_event_twice_makes_one_notification(
    client: AsyncClient, db: AsyncSession
) -> None:
    """릴레이는 at-least-once 다. 같은 이벤트가 두 번 와도 알림은 하나다."""
    guest, body = await _invite_event(client, db)

    await notifier.handle(body)
    await notifier.handle(body)  # 릴레이가 표시 전에 죽어 다시 보낸 것과 같다

    assert await _notifications_of(db, guest["id"]) == 1


# --- durability.py poison ---------------------------------------------------------


async def test_poison_message_does_not_kill_the_loop(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """처리에 실패한 메시지는 지우지 않고(→ 재수신 → DLQ) 다음 메시지로 넘어간다."""
    guest, good = await _invite_event(client, db)
    poison = {
        "event_id": str(uuid4()),
        "event_type": "team.invited",
        "payload": {"invited_email": guest["email"], "invitation_id": "not-a-uuid"},
    }
    no_id = {"event_type": "team.invited", "payload": {}}
    monkeypatch.setattr(consumer, "RECEIVE_BACKOFF_SECONDS", 0)
    queue = FakeQueue(
        [
            [Message("r-poison", poison), Message("r-no-id", no_id)],
            RuntimeError("elasticmq 재시작"),  # 수신 자체가 실패해도 루프는 산다
            [Message("r-good", good)],
        ]
    )

    with pytest.raises(StopConsuming):  # 줄 배치를 다 쓸 때까지 돌았다 = 죽지 않았다
        await consumer.consume(queue, "notify", notifier.handle)

    assert queue.deleted == ["r-good"]  # 실패한 둘은 남는다 — SQS redrive 가 DLQ 로 옮긴다
    assert await _notifications_of(db, guest["id"]) == 1
    # 실패한 처리의 inbox 선점은 롤백됐다 — 재수신되면 다시 시도된다.
    claimed = await db.execute(
        select(ProcessedEvent.event_id).where(ProcessedEvent.consumer == "notifier")
    )
    assert [r[0] for r in claimed] == [UUID(good["event_id"])]


# --- 선점 · 좀비 · 재시도 -----------------------------------------------------------


async def _job(client: AsyncClient, db: AsyncSession, email: str) -> dict:
    ctx = await _queued_extraction(client, email, b"text", "txt")
    ctx["job_id"] = UUID(ctx["job_id"])
    return ctx


async def _backdate(db: AsyncSession, job_id: UUID) -> None:
    """started_at 을 좀비 임계치 너머로 민다."""
    age = timedelta(seconds=settings.job_zombie_seconds_io + 1)
    await db.execute(update(Job).where(Job.id == job_id).values(started_at=func.now() - age))
    await db.commit()


async def _job_row(job_id: UUID) -> Job:
    async with SessionFactory() as session:
        job = await jobs.get(session, job_id)
        assert job is not None
        return job


async def test_only_one_worker_claims_a_job(client: AsyncClient, db: AsyncSession) -> None:
    ctx = await _job(client, db, "claim@example.com")
    async with SessionFactory() as first, SessionFactory() as second:
        won = await jobs.claim(first, ctx["job_id"])
        await first.commit()
        lost = await jobs.claim(second, ctx["job_id"])
    assert won is not None and won.attempt == 1
    assert lost is None


async def test_late_worker_loses_to_the_reaper(client: AsyncClient, db: AsyncSession) -> None:
    """스위퍼가 좀비로 내린 뒤 원래 워커가 돌아와도 결과를 쓰지 못한다.

    status 만 보면 부족하다: 재시도로 다른 워커가 다시 선점하면 status 는 또 running 이다.
    그래서 선점 때 올린 attempt 를 함께 대조한다.
    """
    ctx = await _job(client, db, "zombie@example.com")
    job_id = ctx["job_id"]
    async with SessionFactory() as session:
        stale = await jobs.claim(session, job_id)
        await session.commit()
    assert stale is not None and stale.attempt == 1
    await _backdate(db, job_id)

    async with SessionFactory() as session:
        assert await sweeper.reap_zombies(session) == 1
        await session.commit()
    reaped = await _job_row(job_id)
    assert (reaped.status, reaped.attempt) == ("queued", 1)  # 인프라 실패 → 재시도
    assert reaped.error == {"code": jobs.TIMEOUT, "message": reaped.error["message"]}

    async with SessionFactory() as session:
        # 원래 워커가 돌아왔다 — status 가 queued 라 진다.
        assert not await jobs.finish(session, job_id, 1, "completed", result={"chars": 1})
        fresh = await jobs.claim(session, job_id)
        await session.commit()
    assert fresh is not None and fresh.attempt == 2

    async with SessionFactory() as session:
        # 다른 워커가 다시 선점해 status 는 running 이다. attempt 가 달라 여전히 진다.
        assert not await jobs.finish(session, job_id, 1, "completed", result={"chars": 1})
        assert await jobs.finish(session, job_id, 2, "completed", result={"chars": 4})
        await session.commit()
    assert (await _job_row(job_id)).status == "completed"


async def test_reaper_does_not_touch_live_jobs(client: AsyncClient, db: AsyncSession) -> None:
    ctx = await _job(client, db, "alive@example.com")
    async with SessionFactory() as session:
        await jobs.claim(session, ctx["job_id"])
        await session.commit()
    async with SessionFactory() as session:
        assert await sweeper.reap_zombies(session) == 0
    assert (await _job_row(ctx["job_id"])).status == "running"


async def test_attempts_run_out_and_the_manuscript_fails(
    client: AsyncClient, db: AsyncSession
) -> None:
    ctx = await _job(client, db, "exhaust@example.com")
    job_id = ctx["job_id"]
    for attempt in range(1, 4):  # max_attempt = 3
        async with SessionFactory() as session:
            assert (await jobs.claim(session, job_id)).attempt == attempt  # type: ignore[union-attr]
            await session.commit()
        await _backdate(db, job_id)
        async with SessionFactory() as session:
            await sweeper.reap_zombies(session)
            await session.commit()

    job = await _job_row(job_id)
    assert (job.status, job.attempt) == ("failed", 3)
    async with SessionFactory() as session:
        assert await jobs.retry(session, job_id) is None  # 시도가 남지 않았다
    manuscript = await db.get(Manuscript, UUID(ctx["mid"]))
    assert manuscript is not None and manuscript.status == "failed"


async def test_storage_failure_is_retried_and_announced_again(
    client: AsyncClient, db: AsyncSession
) -> None:
    ctx = await _job(client, db, "s3down@example.com")
    FakeStorage.uploaded.clear()  # 읽기가 실패한다 = 인프라 실패

    assert await handle(SessionFactory, ctx["job_id"], FakeStorage(), FileExtractor()) == "retried"

    job = await _job_row(ctx["job_id"])
    assert (job.status, job.attempt, job.error["code"]) == ("queued", 1, STORAGE_FAILED)  # type: ignore[index]
    announced = await db.execute(
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.aggregate_id == ctx["job_id"], OutboxEvent.event_type == "job.queued")
    )
    assert announced.scalar_one() == 2  # 콜백 때 한 번 + 재시도 때 한 번
    manuscript = await db.get(Manuscript, UUID(ctx["mid"]))
    assert manuscript is not None and manuscript.status == "processing"  # 아직 끝나지 않았다


# --- 업로드 콜백 유실 스위퍼 --------------------------------------------------------


async def _issued_upload(client: AsyncClient, email: str) -> tuple[str, str, str]:
    user = await signup(client, email)
    pid = (
        await client.post(
            "/projects", json={"title": "P", "owner_type": "personal"}, headers=user["headers"]
        )
    ).json()["data"]["project_id"]
    mid = (
        await client.post(
            f"/projects/{pid}/manuscripts",
            json={"title": "초고", "source_type": "upload"},
            headers=user["headers"],
        )
    ).json()["data"]["manuscript_id"]
    key = (
        await client.post(
            f"/projects/{pid}/manuscripts/{mid}/file",
            json={"file_format": "txt"},
            headers=user["headers"],
        )
    ).json()["data"]["file_key"]
    return pid, mid, key


async def test_sweeper_starts_extraction_when_the_callback_was_lost(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, lost, key = await _issued_upload(client, "lost@example.com")
    _, never, _ = await _issued_upload(client, "never@example.com")
    FakeStorage.uploaded[key] = b"uploaded but no callback"  # never 는 올리지 않았다

    async with SessionFactory() as session:
        # 발급 직후는 대상이 아니다.
        assert (
            await manuscripts.sweep_lost_uploads(session, FakeStorage(), timedelta(hours=1)) == []
        )
        started = await manuscripts.sweep_lost_uploads(session, FakeStorage(), timedelta(0))
        await session.commit()
    assert started == [UUID(lost)]

    swept = await db.get(Manuscript, UUID(lost))
    untouched = await db.get(Manuscript, UUID(never))
    assert swept is not None and swept.status == "processing" and swept.extraction_job_id
    assert untouched is not None and (untouched.status, untouched.extraction_job_id) == (
        "draft",
        None,
    )

    # 이미 잡이 있으니 다시 돌려도 하나 더 만들지 않는다.
    async with SessionFactory() as session:
        assert await manuscripts.sweep_lost_uploads(session, FakeStorage(), timedelta(0)) == []
