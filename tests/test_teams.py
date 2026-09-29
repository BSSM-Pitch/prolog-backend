from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.models import OutboxEvent
from app.platform_.teams.models import TeamInvitation
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
            "username": user["username"],
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

    # 틀린 토큰은 "그런 초대는 없다"로 답한다 (§6.4).
    wrong = await client.post(
        f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
        json={"token": "not-the-token"},
        headers=outsider["headers"],
    )
    assert wrong.status_code == 404
    assert code(wrong) == "TEAM_INVITATION_NOT_FOUND"

    accepted = await client.post(
        f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
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
        json={"token": invitation["token"]},
        headers=guest["headers"],
    )
    assert replay.status_code == 409
    assert code(replay) == "INVITATION_NOT_PENDING"

    types = [e.event_type for e in (await db.execute(select(OutboxEvent))).scalars()]
    assert types == ["team.invited", "team.member_joined"]


async def test_revoke_invitation(client: AsyncClient) -> None:
    """초대자의 취소(DELETE)만 있다. 거절 엔드포인트는 명세에 없다 (CLAUDE.md §6.5)."""
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
    assert (
        await client.post(
            f"/teams/{team_id}/invitations/{first['invitation_id']}/reject",
            headers=guest["headers"],
        )
    ).status_code == 404  # 라우트 자체가 없다

    assert (
        await client.delete(
            f"/teams/{team_id}/invitations/{first['invitation_id']}", headers=owner["headers"]
        )
    ).status_code == 204
    stale = await client.delete(
        f"/teams/{team_id}/invitations/{first['invitation_id']}", headers=owner["headers"]
    )
    assert code(stale) == "INVITATION_NOT_PENDING"

    # 취소 후에는 pending 이 없으므로 재초대가 가능하다.
    second = await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": "revguest@example.com"},
        headers=owner["headers"],
    )
    assert second.status_code == 201

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
            json={"token": invitation["token"]},
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


async def test_team_response_schemas_match_spec(client: AsyncClient) -> None:
    """응답 필드 집합을 명세와 대조한다 (TEAM 명세 §2.1~2.3 · §4.6 · §4.9)."""
    owner = await signup(client, "schema@example.com")
    guest = await signup(client, "schemaguest@example.com")

    created = (await client.post("/teams", json={"name": "S"}, headers=owner["headers"])).json()
    assert set(created["data"]) == {
        "team_id",
        "name",
        "description",
        "created_by",
        "member_count",
        "created_at",
        "updated_at",
    }
    team_id = created["data"]["team_id"]
    detail = await client.get(f"/teams/{team_id}", headers=owner["headers"])
    assert set(detail.json()["data"]) == set(created["data"])

    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": guest["email"], "role": "member"},
            headers=owner["headers"],
        )
    ).json()["data"]
    assert set(invitation) == {
        "invitation_id",
        "team_id",
        "invited_email",
        "role",
        "status",
        "expires_at",
        "created_at",
        "token",  # 원문 토큰은 생성 응답에만 (§6.4)
    }

    accepted = (
        await client.post(
            f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
            json={"token": invitation["token"]},
            headers=guest["headers"],
        )
    ).json()["data"]
    # 명세 §4.9 의 응답은 TeamMember 다 (사용자 이름 포함은 목록 쪽 요구사항).
    assert set(accepted) == {"team_id", "user_id", "role", "joined_at"}

    members = (await client.get(f"/teams/{team_id}/members", headers=owner["headers"])).json()[
        "data"
    ]
    assert {m["username"] for m in members} == {owner["username"], guest["username"]}
    assert set(members[0]) == {"team_id", "user_id", "username", "role", "joined_at"}


async def test_inviting_an_existing_member_is_409(client: AsyncClient) -> None:
    """명세 §4.7: 이미 팀원이면 ALREADY_TEAM_MEMBER. 초대 생성 시점에 막는다."""
    owner = await signup(client, "dupmem@example.com")
    team_id = await create_team(client, owner["headers"])

    res = await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": owner["email"], "role": "member"},
        headers=owner["headers"],
    )
    assert res.status_code == 409
    assert code(res) == "ALREADY_TEAM_MEMBER"

    # 미가입 이메일은 초대된다 (명세 §4.7 비고).
    fresh = await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": "nobody@example.com", "role": "member"},
        headers=owner["headers"],
    )
    assert fresh.status_code == 201


async def test_member_count_is_aggregated(client: AsyncClient) -> None:
    """teams.member_count 는 저장하지 않고 조회 시 집계한다 (CLAUDE.md §7)."""
    owner = await signup(client, "cnt@example.com")
    guest = await signup(client, "cntguest@example.com")
    team_id = await create_team(client, owner["headers"])

    assert (await client.get(f"/teams/{team_id}", headers=owner["headers"])).json()["data"][
        "member_count"
    ] == 1

    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": guest["email"], "role": "member"},
            headers=owner["headers"],
        )
    ).json()["data"]
    await client.post(
        f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=guest["headers"],
    )

    assert (await client.get(f"/teams/{team_id}", headers=owner["headers"])).json()["data"][
        "member_count"
    ] == 2
    listed = (await client.get("/teams", headers=guest["headers"])).json()["data"]
    assert [t["member_count"] for t in listed] == [2]


async def test_invitation_cannot_grant_owner(client: AsyncClient) -> None:
    """초대로 부여할 수 있는 역할은 admin|member 다 (명세 §2.3)."""
    owner = await signup(client, "noowner@example.com")
    team_id = await create_team(client, owner["headers"])
    res = await client.post(
        f"/teams/{team_id}/invitations",
        json={"invited_email": "x@example.com", "role": "owner"},
        headers=owner["headers"],
    )
    assert res.status_code == 400
    assert code(res) == "INVALID_INPUT"


async def test_team_projects_endpoint(client: AsyncClient) -> None:
    """TEAM 명세 §4.13 — 팀 소속 프로젝트 목록."""
    owner = await signup(client, "tp@example.com")
    outsider = await signup(client, "tpout@example.com")
    team_id = await create_team(client, owner["headers"])

    await client.post(
        "/projects",
        json={"title": "팀 것", "owner_type": "team", "team_id": team_id},
        headers=owner["headers"],
    )
    await client.post(
        "/projects", json={"title": "개인 것", "owner_type": "personal"}, headers=owner["headers"]
    )

    listed = await client.get(f"/teams/{team_id}/projects", headers=owner["headers"])
    assert listed.status_code == 200
    assert [p["title"] for p in listed.json()["data"]] == ["팀 것"]
    assert listed.json()["meta"] == {"next_cursor": None}

    # 비소속자는 팀 경로 자체를 못 본다
    denied = await client.get(f"/teams/{team_id}/projects", headers=outsider["headers"])
    assert denied.status_code == 403


async def test_invitation_token_not_email_decides_acceptance(client: AsyncClient) -> None:
    """초대받은 주소와 로그인 주소가 달라도 토큰만 맞으면 수락된다 (CLAUDE.md §6.4).

    회사 메일로 초대받고 개인 지메일로 로그인하는 경우가 흔하다.
    """
    owner = await signup(client, "tok@example.com")
    guest = await signup(client, "personal@gmail.example")
    team_id = await create_team(client, owner["headers"])

    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": "work@company.example", "role": "member"},
            headers=owner["headers"],
        )
    ).json()["data"]
    assert invitation["invited_email"] == "work@company.example"

    accepted = await client.post(
        f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=guest["headers"],
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"]["user_id"] == guest["id"]


async def test_expired_invitation_is_410(client: AsyncClient, db: AsyncSession) -> None:
    """CLAUDE.md §10 — 만료 초대 수락은 410 INVITATION_EXPIRED."""
    owner = await signup(client, "exp@example.com")
    guest = await signup(client, "expguest@example.com")
    team_id = await create_team(client, owner["headers"])

    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": guest["email"], "role": "member"},
            headers=owner["headers"],
        )
    ).json()["data"]
    await db.execute(
        update(TeamInvitation)
        .where(TeamInvitation.id == UUID(invitation["invitation_id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await db.commit()

    res = await client.post(
        f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=guest["headers"],
    )
    assert res.status_code == 410
    assert code(res) == "INVITATION_EXPIRED"


async def test_list_invitations(client: AsyncClient) -> None:
    """TEAM 명세 §4.8 — owner|admin 만 조회. 원문 토큰은 목록에 없다 (§6.4)."""
    owner = await signup(client, "li@example.com")
    guest = await signup(client, "liguest@example.com")
    team_id = await create_team(client, owner["headers"])

    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": guest["email"], "role": "member"},
            headers=owner["headers"],
        )
    ).json()["data"]

    listed = await client.get(f"/teams/{team_id}/invitations", headers=owner["headers"])
    assert listed.status_code == 200
    rows = listed.json()["data"]
    assert [r["invitation_id"] for r in rows] == [invitation["invitation_id"]]
    assert "token" not in rows[0]

    # 멤버가 아닌 사용자는 팀 경로 자체가 막힌다
    assert (
        await client.get(f"/teams/{team_id}/invitations", headers=guest["headers"])
    ).status_code == 403


async def test_wrong_token_is_404_whatever_the_invitation_state(client: AsyncClient) -> None:
    """토큰을 상태보다 먼저 본다. 순서가 반대면 토큰 없이도 초대가 처리됐는지 알 수 있다."""
    owner = await signup(client, "ws@example.com")
    guest = await signup(client, "wsguest@example.com")
    team_id = await create_team(client, owner["headers"])
    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": guest["email"]},
            headers=owner["headers"],
        )
    ).json()["data"]
    url = f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept"

    pending = await client.post(url, json={"token": "wrong"}, headers=guest["headers"])
    assert pending.status_code == 404
    accepted = await client.post(url, json={"token": invitation["token"]}, headers=guest["headers"])
    assert accepted.status_code == 200
    after = await client.post(url, json={"token": "wrong"}, headers=guest["headers"])
    assert (after.status_code, code(after)) == (404, "TEAM_INVITATION_NOT_FOUND")
    # 맞는 토큰이면 상태를 알려 준다 — 소지자에게는 숨길 이유가 없다.
    replay = await client.post(url, json={"token": invitation["token"]}, headers=guest["headers"])
    assert code(replay) == "INVITATION_NOT_PENDING"
