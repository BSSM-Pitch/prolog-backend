from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.models import OutboxEvent
from tests.conftest import code, signup


async def create_team(client: AsyncClient, headers: dict, name: str = "팀") -> str:
    res = await client.post("/teams", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]["team_id"]


async def test_create_team_makes_creator_owner(client: AsyncClient) -> None:
    user = await signup(client, "owner@example.com")
    team_id = await create_team(client, user["headers"])

    members = await client.get(f"/teams/{team_id}/members", headers=user["headers"])
    assert members.status_code == 200
    assert members.json()["data"] == [
        {
            "team_id": team_id,
            "user_id": user["id"],
            "role": "owner",
            "joined_at": members.json()["data"][0]["joined_at"],
        }
    ]


async def test_team_crud_and_permissions(client: AsyncClient) -> None:
    owner = await signup(client, "o@example.com")
    stranger = await signup(client, "s@example.com")
    team_id = await create_team(client, owner["headers"])

    got = await client.get(f"/teams/{team_id}", headers=owner["headers"])
    assert got.json()["data"]["name"] == "팀"

    patched = await client.patch(
        f"/teams/{team_id}", json={"name": "새 팀"}, headers=owner["headers"]
    )
    assert patched.json()["data"]["name"] == "새 팀"

    denied = await client.get(f"/teams/{team_id}", headers=stranger["headers"])
    assert denied.status_code == 403
    assert code(denied) == "FORBIDDEN"

    missing = await client.get(
        "/teams/00000000-0000-0000-0000-000000000000", headers=owner["headers"]
    )
    assert missing.status_code == 404
    assert code(missing) == "TEAM_NOT_FOUND"

    assert (await client.delete(f"/teams/{team_id}", headers=owner["headers"])).status_code == 204


async def test_team_list_is_cursor_paginated(client: AsyncClient) -> None:
    user = await signup(client, "page@example.com")
    for i in range(3):
        await create_team(client, user["headers"], f"팀{i}")

    first = await client.get("/teams?limit=2", headers=user["headers"])
    body = first.json()
    assert len(body["data"]) == 2
    assert body["meta"]["next_cursor"]

    second = await client.get(
        f"/teams?limit=2&cursor={body['meta']['next_cursor']}", headers=user["headers"]
    )
    assert len(second.json()["data"]) == 1
    assert second.json()["meta"]["next_cursor"] is None

    bad = await client.get("/teams?cursor=!!!", headers=user["headers"])
    assert bad.status_code == 400
    assert code(bad) == "INVALID_INPUT"


async def test_invite_writes_outbox_event_in_same_transaction(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await signup(client, "inviter@example.com")
    team_id = await create_team(client, owner["headers"])

    res = await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": "friend@example.com", "role": "member"},
        headers=owner["headers"],
    )
    assert res.status_code == 201
    assert res.json()["data"]["status"] == "pending"

    events = list((await db.execute(select(OutboxEvent))).scalars())
    assert [e.event_type for e in events] == ["team.invited"]
    assert events[0].payload["invited_email"] == "friend@example.com"
    assert events[0].published_at is None  # 릴레이 워커가 찍는다 (Phase 1)


async def test_duplicate_pending_invitation_is_409(client: AsyncClient) -> None:
    owner = await signup(client, "dupinv@example.com")
    team_id = await create_team(client, owner["headers"])
    payload = {"invited_email": "friend@example.com", "role": "member"}
    assert (
        await client.post(f"/teams/{team_id}/invitations", json=payload, headers=owner["headers"])
    ).status_code == 201
    again = await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": "FRIEND@example.com", "role": "member"},
        headers=owner["headers"],
    )
    assert again.status_code == 409
    assert code(again) == "DUPLICATE_INVITATION"


async def test_accept_invitation_adds_member(client: AsyncClient, db: AsyncSession) -> None:
    owner = await signup(client, "host@example.com")
    guest = await signup(client, "guest@example.com")
    outsider = await signup(client, "outsider@example.com")
    team_id = await create_team(client, owner["headers"])

    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": "guest@example.com", "role": "admin"},
            headers=owner["headers"],
        )
    ).json()["data"]

    wrong = await client.post(
        f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
        headers=outsider["headers"],
    )
    assert wrong.status_code == 403
    assert code(wrong) == "INVITATION_EMAIL_MISMATCH"

    accepted = await client.post(
        f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
        headers=guest["headers"],
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"] == {
        "team_id": team_id,
        "user_id": guest["id"],
        "role": "admin",
        "joined_at": accepted.json()["data"]["joined_at"],
    }

    members = (await client.get(f"/teams/{team_id}/members", headers=guest["headers"])).json()
    assert {m["user_id"]: m["role"] for m in members["data"]} == {
        owner["id"]: "owner",
        guest["id"]: "admin",
    }

    replay = await client.post(
        f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
        headers=guest["headers"],
    )
    assert replay.status_code == 409
    assert code(replay) == "INVITATION_NOT_PENDING"

    types = [e.event_type for e in (await db.execute(select(OutboxEvent))).scalars()]
    assert types == ["team.invited", "team.member_joined"]


async def test_reject_and_revoke_invitation(client: AsyncClient) -> None:
    owner = await signup(client, "rev@example.com")
    guest = await signup(client, "revguest@example.com")
    team_id = await create_team(client, owner["headers"])

    first = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": "revguest@example.com"},
            headers=owner["headers"],
        )
    ).json()["data"]
    rejected = await client.post(
        f"/teams/{team_id}/invitations/{first['invitation_id']}/reject", headers=guest["headers"]
    )
    assert rejected.json()["data"]["status"] == "rejected"

    # 거절 후에는 pending 이 없으므로 재초대가 가능하다.
    second = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": "revguest@example.com"},
            headers=owner["headers"],
        )
    ).json()["data"]
    assert (
        await client.delete(
            f"/teams/{team_id}/invitations/{second['invitation_id']}", headers=owner["headers"]
        )
    ).status_code == 204
    stale = await client.delete(
        f"/teams/{team_id}/invitations/{second['invitation_id']}", headers=owner["headers"]
    )
    assert code(stale) == "INVITATION_NOT_PENDING"

    unknown = await client.delete(
        f"/teams/{team_id}/invitations/00000000-0000-0000-0000-000000000000",
        headers=owner["headers"],
    )
    assert code(unknown) == "TEAM_INVITATION_NOT_FOUND"


async def test_last_owner_cannot_be_removed_or_demoted(client: AsyncClient) -> None:
    owner = await signup(client, "solo@example.com")
    team_id = await create_team(client, owner["headers"])

    demote = await client.patch(
        f"/teams/{team_id}/members/{owner['id']}", json={"role": "member"}, headers=owner["headers"]
    )
    assert demote.status_code == 409
    assert code(demote) == "LAST_OWNER_CANNOT_LEAVE"

    remove = await client.delete(
        f"/teams/{team_id}/members/{owner['id']}", headers=owner["headers"]
    )
    assert code(remove) == "LAST_OWNER_CANNOT_LEAVE"

    ghost = await client.delete(
        f"/teams/{team_id}/members/00000000-0000-0000-0000-000000000000",
        headers=owner["headers"],
    )
    assert code(ghost) == "TEAM_MEMBER_NOT_FOUND"


async def test_team_with_projects_cannot_be_deleted(client: AsyncClient) -> None:
    owner = await signup(client, "busy@example.com")
    team_id = await create_team(client, owner["headers"])
    created = await client.post(
        "/projects",
        json={"title": "팀 프로젝트", "owner_type": "team", "team_id": team_id},
        headers=owner["headers"],
    )
    assert created.status_code == 201

    res = await client.delete(f"/teams/{team_id}", headers=owner["headers"])
    assert res.status_code == 409
    assert code(res) == "TEAM_HAS_ACTIVE_PROJECTS"


async def test_member_can_leave_but_cannot_remove_others(client: AsyncClient) -> None:
    """본인 탈퇴 또는 owner|admin 의 팀원 제거 (TEAM 명세 §4.12)."""
    owner = await signup(client, "leaveowner@example.com")
    guest = await signup(client, "leaveguest@example.com")
    other = await signup(client, "leaveother@example.com")
    team_id = await create_team(client, owner["headers"])
    for who in (guest, other):
        invitation = (
            await client.post(
                f"/teams/{team_id}/invitations",
                json={"invited_email": who["email"], "role": "member"},
                headers=owner["headers"],
            )
        ).json()["data"]
        await client.post(
            f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
            headers=who["headers"],
        )

    # member 는 남을 제거하지 못한다
    denied = await client.delete(
        f"/teams/{team_id}/members/{other['id']}", headers=guest["headers"]
    )
    assert denied.status_code == 403
    assert code(denied) == "FORBIDDEN"

    # 본인은 나갈 수 있다
    left = await client.delete(f"/teams/{team_id}/members/{guest['id']}", headers=guest["headers"])
    assert left.status_code == 204
    assert (await client.get(f"/teams/{team_id}", headers=guest["headers"])).status_code == 403
