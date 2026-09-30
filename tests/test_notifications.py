"""NOTI — 인앱 알림. 생성 경로는 Outbox 이벤트뿐이다 (명세 §3: 공개 생성 API 없음)."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.notify import notify_from_event
from app.events.relay import relay_once
from tests.conftest import FakeQueue, code, signup


async def _invite_and_notify(
    client: AsyncClient, db: AsyncSession, owner: dict, invited_email: str
) -> dict:
    """팀 초대 → Outbox → 릴레이 → 알림. Phase 1 사슬 전체를 탄다."""
    team_id = (await client.post("/teams", json={"name": "N"}, headers=owner["headers"])).json()[
        "data"
    ]["team_id"]
    await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": invited_email, "role": "member"},
        headers=owner["headers"],
    )
    queue = FakeQueue()
    assert await relay_once(db, queue, batch=10) == 1
    created = await notify_from_event(db, queue.sent[0][1])
    await db.commit()
    assert created is not None
    return created.model_dump()


async def test_invite_event_becomes_an_in_app_notification(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await signup(client, "no@example.com")
    guest = await signup(client, "ng@example.com")
    created = await _invite_and_notify(client, db, owner, guest["email"])
    assert created["type"] == "team_invite"

    listed = (await client.get("/notifications", headers=guest["headers"])).json()
    assert listed["meta"]["unread_count"] == 1
    assert listed["meta"]["next_cursor"] is None
    row = listed["data"][0]
    assert set(row) == {
        "notification_id",
        "user_id",
        "type",
        "title",
        "body",
        "related_ref",
        "channels_sent",
        "read_at",
        "created_at",
    }
    assert row["type"] == "team_invite"
    assert row["related_ref"]["type"] == "team_invitation"
    # 설정값이 아니라 실제 발송 결과다. 메일 경로가 없으니 in_app 뿐이다.
    assert row["channels_sent"] == ["in_app"]
    assert row["read_at"] is None

    # 남의 알림은 보이지 않는다.
    assert (await client.get("/notifications", headers=owner["headers"])).json()["data"] == []


async def test_unregistered_invitee_gets_no_notification(
    client: AsyncClient, db: AsyncSession
) -> None:
    """미가입 이메일은 인앱 대상이 없다. 메일 유도는 발송 경로가 생긴 뒤의 일이다."""
    owner = await signup(client, "nu@example.com")
    team_id = (await client.post("/teams", json={"name": "N"}, headers=owner["headers"])).json()[
        "data"
    ]["team_id"]
    await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": "nobody@example.com", "role": "member"},
        headers=owner["headers"],
    )
    queue = FakeQueue()
    await relay_once(db, queue, batch=10)
    assert await notify_from_event(db, queue.sent[0][1]) is None


async def test_unknown_event_is_ignored(db: AsyncSession) -> None:
    assert await notify_from_event(db, {"event_type": "team.member_joined", "payload": {}}) is None


async def test_read_flow(client: AsyncClient, db: AsyncSession) -> None:
    owner = await signup(client, "nr@example.com")
    guest = await signup(client, "nrg@example.com")
    created = await _invite_and_notify(client, db, owner, guest["email"])
    nid = created["notification_id"]

    detail = await client.get(f"/notifications/{nid}", headers=guest["headers"])
    assert detail.status_code == 200

    read = await client.patch(
        f"/notifications/{nid}", json={"read": True}, headers=guest["headers"]
    )
    assert read.json()["data"]["read_at"] is not None
    listed = await client.get("/notifications?unread_only=true", headers=guest["headers"])
    assert listed.json()["data"] == []
    assert listed.json()["meta"]["unread_count"] == 0

    # 되돌릴 수도 있다 (boolean 이므로).
    back = await client.patch(
        f"/notifications/{nid}", json={"read": False}, headers=guest["headers"]
    )
    assert back.json()["data"]["read_at"] is None

    bulk = await client.patch("/notifications/read-all", headers=guest["headers"])
    assert bulk.json()["data"] == {"updated_count": 1}
    assert (await client.patch("/notifications/read-all", headers=guest["headers"])).json()[
        "data"
    ] == {"updated_count": 0}


async def test_other_users_notification_is_404(client: AsyncClient, db: AsyncSession) -> None:
    owner = await signup(client, "nx@example.com")
    guest = await signup(client, "nxg@example.com")
    created = await _invite_and_notify(client, db, owner, guest["email"])
    nid = created["notification_id"]

    # 명세 §1.4: 타인의 알림도 NOTIFICATION_NOT_FOUND 다(존재를 알려주지 않는다).
    stolen = await client.get(f"/notifications/{nid}", headers=owner["headers"])
    assert stolen.status_code == 404
    assert code(stolen) == "NOTIFICATION_NOT_FOUND"

    assert (
        await client.delete(f"/notifications/{nid}", headers=owner["headers"])
    ).status_code == 404
    assert (
        await client.delete(f"/notifications/{nid}", headers=guest["headers"])
    ).status_code == 204
    assert (await client.get("/notifications", headers=guest["headers"])).json()["data"] == []


async def test_settings_default_and_per_type_update(client: AsyncClient) -> None:
    """화면 31: 유형마다 서비스 알림 / 이메일을 따로 켜고 끈다."""
    user = await signup(client, "ns1@example.com")
    h = user["headers"]
    res = await client.get("/users/me/notification-settings", headers=h)
    assert res.json()["data"] == [
        {"type": t, "in_app_enabled": True, "email_enabled": True}
        for t in ("team_invite", "project_invite", "team_joined", "mention", "system")
    ]

    res = await client.patch(
        "/users/me/notification-settings",
        json={"type": "mention", "email_enabled": False},
        headers=h,
    )
    assert res.json()["data"] == {"type": "mention", "in_app_enabled": True, "email_enabled": False}
    res = await client.patch(
        "/users/me/notification-settings",
        json={"type": "mention", "in_app_enabled": False},
        headers=h,
    )
    # 보내지 않은 채널은 그대로다
    assert res.json()["data"] == {
        "type": "mention",
        "in_app_enabled": False,
        "email_enabled": False,
    }
    listed = (await client.get("/users/me/notification-settings", headers=h)).json()["data"]
    assert [s for s in listed if s["type"] != "mention"] == [
        {"type": t, "in_app_enabled": True, "email_enabled": True}
        for t in ("team_invite", "project_invite", "team_joined", "system")
    ]

    res = await client.patch(
        "/users/me/notification-settings",
        json={"type": "digest", "in_app_enabled": False},
        headers=h,
    )
    assert code(res) == "INVALID_INPUT"


async def test_in_app_off_suppresses_that_type(client: AsyncClient, db: AsyncSession) -> None:
    owner = await signup(client, "ns-owner@example.com")
    invitee = await signup(client, "ns-invitee@example.com")
    await client.patch(
        "/users/me/notification-settings",
        json={"type": "team_invite", "in_app_enabled": False},
        headers=invitee["headers"],
    )
    team_id = (await client.post("/teams", json={"name": "N"}, headers=owner["headers"])).json()[
        "data"
    ]["team_id"]
    await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": "ns-invitee@example.com", "role": "member"},
        headers=owner["headers"],
    )
    queue = FakeQueue()
    assert await relay_once(db, queue, batch=10) == 1
    assert await notify_from_event(db, queue.sent[0][1]) is None
    assert (await client.get("/notifications", headers=invitee["headers"])).json()["data"] == []
