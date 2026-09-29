from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform_.projects.models import ProjectInvitation
from tests.conftest import code, signup


async def create_project(client: AsyncClient, headers: dict, **kwargs: object) -> dict:
    body = {"title": "프로젝트", "owner_type": "personal"} | kwargs
    res = await client.post("/projects", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def test_create_personal_project(client: AsyncClient) -> None:
    user = await signup(client, "p1@example.com")
    project = await create_project(client, user["headers"])
    assert project["owner_type"] == "personal"
    assert project["team_id"] is None

    members = await client.get(
        f"/projects/{project['project_id']}/members", headers=user["headers"]
    )
    assert members.json()["data"][0] == {
        "project_id": project["project_id"],
        "user_id": user["id"],
        "username": user["username"],
        "role": "owner",
        "joined_at": members.json()["data"][0]["joined_at"],
        "source": "project",
    }


async def test_owner_type_and_team_id_must_agree(client: AsyncClient) -> None:
    user = await signup(client, "p2@example.com")
    res = await client.post(
        "/projects", json={"title": "x", "owner_type": "team"}, headers=user["headers"]
    )
    assert res.status_code == 400
    assert code(res) == "INVALID_INPUT"


async def join_team(client: AsyncClient, owner: dict, guest: dict, team_id: str, role: str) -> None:
    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"invited_email": guest["email"], "role": role},
            headers=owner["headers"],
        )
    ).json()["data"]
    res = await client.post(
        f"/teams/{team_id}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=guest["headers"],
    )
    assert res.status_code == 200, res.text


async def test_any_team_member_can_create_team_project(client: AsyncClient) -> None:
    """팀 프로젝트 생성에 요구되는 것은 팀 소속 여부뿐이다 (CLAUDE.md §6.2).

    이전 구현은 owner|admin 을 요구했고 테스트가 그 위반을 고정하고 있었다.
    """
    owner = await signup(client, "towner@example.com")
    member = await signup(client, "tmember@example.com")
    outsider = await signup(client, "nope@example.com")
    team_id = (await client.post("/teams", json={"name": "팀"}, headers=owner["headers"])).json()[
        "data"
    ]["team_id"]
    await join_team(client, owner, member, team_id, "member")

    project = await create_project(
        client, member["headers"], owner_type="team", team_id=team_id, title="팀원이 만든다"
    )
    assert project["team_id"] == team_id

    # 비소속자는 일반 FORBIDDEN 이 아니라 NOT_TEAM_MEMBER 다 (§6.1).
    stranger = await client.post(
        "/projects",
        json={"title": "몰래", "owner_type": "team", "team_id": team_id},
        headers=outsider["headers"],
    )
    assert stranger.status_code == 403
    assert code(stranger) == "NOT_TEAM_MEMBER"

    # 없는 팀이면 TEAM_NOT_FOUND (PRJ 명세 §1.4).
    ghost = await client.post(
        "/projects",
        json={
            "title": "유령",
            "owner_type": "team",
            "team_id": "00000000-0000-0000-0000-000000000000",
        },
        headers=member["headers"],
    )
    assert ghost.status_code == 404
    assert code(ghost) == "TEAM_NOT_FOUND"


async def test_team_project_is_not_locked_out_from_the_team(client: AsyncClient) -> None:
    """감사에서 실측한 락아웃 8종의 회귀 방지.

    팀 프로젝트는 `project_members` 에 생성자만 들어간다. 팀원의 권한은
    `project_role_of` 가 `team_members` 를 함께 읽어 해석한다 — 그게 깨지면
    팀 프로젝트가 개인 프로젝트로 퇴화한다.
    """
    owner = await signup(client, "lockowner@example.com")
    admin = await signup(client, "lockadmin@example.com")
    member = await signup(client, "lockmember@example.com")
    outsider = await signup(client, "lockout@example.com")
    team_id = (await client.post("/teams", json={"name": "T"}, headers=owner["headers"])).json()[
        "data"
    ]["team_id"]
    await join_team(client, owner, admin, team_id, "admin")
    await join_team(client, owner, member, team_id, "member")

    # [1] 일반 팀원도 만든다  [2] admin 도 만든다
    await create_project(client, member["headers"], owner_type="team", team_id=team_id, title="M")
    project = await create_project(
        client, admin["headers"], owner_type="team", team_id=team_id, title="A"
    )
    pid = project["project_id"]

    # [3] 생성자 [4] 팀 owner [5] 다른 팀원 — 전원 조회된다
    for actor in (admin, owner, member):
        assert (await client.get(f"/projects/{pid}", headers=actor["headers"])).status_code == 200

    # 팀원은 editor 다: 수정은 되고 삭제·초대는 안 된다 (TEAM_MEMBER_PROJECT_ROLE)
    edited = await client.patch(
        f"/projects/{pid}", json={"description": "팀원이 고친다"}, headers=member["headers"]
    )
    assert edited.status_code == 200
    assert (await client.delete(f"/projects/{pid}", headers=member["headers"])).status_code == 403
    invited = await client.post(
        f"/projects/{pid}/invitations",
        json={"invited_email": "x@example.com", "role": "viewer"},
        headers=member["headers"],
    )
    assert invited.status_code == 403

    # [6][7] 팀원은 자기가 만들지 않은 팀 프로젝트도 목록에서 본다 (명세 §4.1).
    # created_at 은 트랜잭션 시각이라 M·A 가 동률이다. 순서가 아니라 집합으로 본다.
    for actor in (owner, admin, member):
        listed = await client.get("/projects", headers=actor["headers"])
        assert sorted(p["title"] for p in listed.json()["data"]) == ["A", "M"]

    # 쿼리 필터 (명세 §4.1)
    by_team = await client.get(f"/projects?team_id={team_id}", headers=member["headers"])
    assert sorted(p["title"] for p in by_team.json()["data"]) == ["A", "M"]
    personal_only = await client.get("/projects?owner_type=personal", headers=member["headers"])
    assert personal_only.json()["data"] == []

    # [8] 비소속자는 생성도 조회도 막힌다
    assert (await client.get(f"/projects/{pid}", headers=outsider["headers"])).status_code == 403
    denied = await client.post(
        "/projects",
        json={"title": "몰래", "owner_type": "team", "team_id": team_id},
        headers=outsider["headers"],
    )
    assert code(denied) == "NOT_TEAM_MEMBER"

    # 개인 프로젝트는 팀 해석의 영향을 받지 않는다
    personal = await create_project(client, owner["headers"], title="개인")
    assert (
        await client.get(f"/projects/{personal['project_id']}", headers=member["headers"])
    ).status_code == 403


async def test_project_role_hierarchy(client: AsyncClient) -> None:
    owner = await signup(client, "prole@example.com")
    viewer = await signup(client, "pviewer@example.com")
    project = await create_project(client, owner["headers"])

    invitation = (
        await client.post(
            f"/projects/{project['project_id']}/invitations",
            json={"invited_email": "pviewer@example.com", "role": "viewer"},
            headers=owner["headers"],
        )
    ).json()["data"]
    await client.post(
        f"/projects/{project['project_id']}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=viewer["headers"],
    )

    assert (
        await client.get(f"/projects/{project['project_id']}", headers=viewer["headers"])
    ).status_code == 200
    # viewer < editor → PATCH 금지
    denied = await client.patch(
        f"/projects/{project['project_id']}", json={"title": "고침"}, headers=viewer["headers"]
    )
    assert denied.status_code == 403
    # viewer < owner → 초대 생성 금지
    assert (
        await client.post(
            f"/projects/{project['project_id']}/invitations",
            json={"invited_email": "someone@example.com", "role": "viewer"},
            headers=viewer["headers"],
        )
    ).status_code == 403

    patched = await client.patch(
        f"/projects/{project['project_id']}", json={"title": "고침"}, headers=owner["headers"]
    )
    assert patched.json()["data"]["title"] == "고침"


async def test_project_not_found_and_forbidden(client: AsyncClient) -> None:
    owner = await signup(client, "pnf@example.com")
    stranger = await signup(client, "pstranger@example.com")
    project = await create_project(client, owner["headers"])

    missing = await client.get(
        "/projects/00000000-0000-0000-0000-000000000000", headers=owner["headers"]
    )
    assert missing.status_code == 404
    assert code(missing) == "PROJECT_NOT_FOUND"

    denied = await client.get(f"/projects/{project['project_id']}", headers=stranger["headers"])
    assert denied.status_code == 403
    assert code(denied) == "FORBIDDEN"


async def test_member_role_update_and_removal(client: AsyncClient) -> None:
    owner = await signup(client, "pm@example.com")
    mate = await signup(client, "pmate@example.com")
    project = await create_project(client, owner["headers"])
    invitation = (
        await client.post(
            f"/projects/{project['project_id']}/invitations",
            json={"invited_email": "pmate@example.com", "role": "editor"},
            headers=owner["headers"],
        )
    ).json()["data"]
    await client.post(
        f"/projects/{project['project_id']}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=mate["headers"],
    )

    promoted = await client.patch(
        f"/projects/{project['project_id']}/members/{mate['id']}",
        json={"role": "owner"},
        headers=owner["headers"],
    )
    assert promoted.json()["data"]["role"] == "owner"

    assert (
        await client.delete(
            f"/projects/{project['project_id']}/members/{mate['id']}", headers=owner["headers"]
        )
    ).status_code == 204

    last = await client.delete(
        f"/projects/{project['project_id']}/members/{owner['id']}", headers=owner["headers"]
    )
    assert code(last) == "LAST_OWNER_CANNOT_LEAVE"


async def test_project_delete_cascades_membership(client: AsyncClient) -> None:
    owner = await signup(client, "pdel@example.com")
    project = await create_project(client, owner["headers"])
    assert (
        await client.delete(f"/projects/{project['project_id']}", headers=owner["headers"])
    ).status_code == 204
    assert (await client.get("/projects", headers=owner["headers"])).json()["data"] == []


async def test_phase0_done_criteria(client: AsyncClient) -> None:
    """인증된 사용자가 프로젝트를 만들고 동료를 초대해 수락까지 마친다 (CLAUDE.md §13)."""
    creator = await signup(client, "creator@example.com")
    colleague = await signup(client, "colleague@example.com")

    project = await create_project(client, creator["headers"], title="장편 소설")
    invitation = (
        await client.post(
            f"/projects/{project['project_id']}/invitations",
            json={"invited_email": "colleague@example.com", "role": "editor"},
            headers=creator["headers"],
        )
    ).json()["data"]
    accepted = await client.post(
        f"/projects/{project['project_id']}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=colleague["headers"],
    )
    assert accepted.status_code == 200

    mine = await client.get("/projects", headers=colleague["headers"])
    assert [p["project_id"] for p in mine.json()["data"]] == [project["project_id"]]
    edited = await client.patch(
        f"/projects/{project['project_id']}",
        json={"description": "동료가 고친다"},
        headers=colleague["headers"],
    )
    assert edited.json()["data"]["description"] == "동료가 고친다"


async def test_viewer_can_leave_but_cannot_remove_owner(client: AsyncClient) -> None:
    """본인 탈퇴 또는 owner 의 멤버 제거 (PRJ 명세 §4.11)."""
    owner = await signup(client, "plvowner@example.com")
    viewer = await signup(client, "plvviewer@example.com")
    project = await create_project(client, owner["headers"])
    invitation = (
        await client.post(
            f"/projects/{project['project_id']}/invitations",
            json={"invited_email": viewer["email"], "role": "viewer"},
            headers=owner["headers"],
        )
    ).json()["data"]
    await client.post(
        f"/projects/{project['project_id']}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=viewer["headers"],
    )

    denied = await client.delete(
        f"/projects/{project['project_id']}/members/{owner['id']}", headers=viewer["headers"]
    )
    assert denied.status_code == 403
    assert code(denied) == "FORBIDDEN"

    left = await client.delete(
        f"/projects/{project['project_id']}/members/{viewer['id']}", headers=viewer["headers"]
    )
    assert left.status_code == 204
    assert (
        await client.get(f"/projects/{project['project_id']}", headers=viewer["headers"])
    ).status_code == 403


async def test_project_response_schemas_match_spec(client: AsyncClient) -> None:
    """응답 필드 집합을 명세와 대조한다 (PRJ 명세 §2.1~2.3 · §4.6 · §4.8)."""
    owner = await signup(client, "pschema@example.com")
    mate = await signup(client, "pschemamate@example.com")

    project = await create_project(client, owner["headers"])
    assert set(project) == {
        "project_id",
        "title",
        "description",
        "owner_type",
        "team_id",
        "created_by",
        "manuscript_count",
        "created_at",
        "updated_at",
    }

    invitation = (
        await client.post(
            f"/projects/{project['project_id']}/invitations",
            json={"invited_email": mate["email"], "role": "editor"},
            headers=owner["headers"],
        )
    ).json()["data"]
    assert set(invitation) == {
        "invitation_id",
        "project_id",
        "invited_email",
        "role",
        "status",
        "expires_at",
        "created_at",
        "token",  # 원문 토큰은 생성 응답에만 (§6.4)
    }

    accepted = (
        await client.post(
            f"/projects/{project['project_id']}/invitations/{invitation['invitation_id']}/accept",
            json={"token": invitation["token"]},
            headers=mate["headers"],
        )
    ).json()["data"]
    assert set(accepted) == {"project_id", "user_id", "role", "joined_at"}

    members = (
        await client.get(f"/projects/{project['project_id']}/members", headers=owner["headers"])
    ).json()["data"]
    assert {m["username"] for m in members} == {owner["username"], mate["username"]}
    # source 는 명세에 없다 — 팀 유래 멤버 구분용 (ASSUMPTION, 제안 목록)
    assert set(members[0]) == {"project_id", "user_id", "username", "role", "joined_at", "source"}


async def test_inviting_an_existing_member_is_409(client: AsyncClient) -> None:
    """명세 §4.7: 이미 멤버면 ALREADY_MEMBER. 초대 생성 시점에 막는다."""
    owner = await signup(client, "pdupmem@example.com")
    project = await create_project(client, owner["headers"])

    res = await client.post(
        f"/projects/{project['project_id']}/invitations",
        json={"invited_email": owner["email"], "role": "editor"},
        headers=owner["headers"],
    )
    assert res.status_code == 409
    assert code(res) == "ALREADY_MEMBER"


async def test_invitation_cannot_grant_owner(client: AsyncClient) -> None:
    """초대로 부여할 수 있는 역할은 editor|viewer 다 (명세 §2.3)."""
    owner = await signup(client, "pnoowner@example.com")
    project = await create_project(client, owner["headers"])
    res = await client.post(
        f"/projects/{project['project_id']}/invitations",
        json={"invited_email": "x@example.com", "role": "owner"},
        headers=owner["headers"],
    )
    assert res.status_code == 400
    assert code(res) == "INVALID_INPUT"


async def test_expired_invitation_is_410(client: AsyncClient, db: AsyncSession) -> None:
    """CLAUDE.md §10 — 만료 초대 수락은 410 INVITATION_EXPIRED."""
    owner = await signup(client, "pexp@example.com")
    guest = await signup(client, "pexpguest@example.com")
    project = await create_project(client, owner["headers"])

    invitation = (
        await client.post(
            f"/projects/{project['project_id']}/invitations",
            json={"invited_email": guest["email"], "role": "editor"},
            headers=owner["headers"],
        )
    ).json()["data"]
    await db.execute(
        update(ProjectInvitation)
        .where(ProjectInvitation.id == UUID(invitation["invitation_id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await db.commit()

    res = await client.post(
        f"/projects/{project['project_id']}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=guest["headers"],
    )
    assert res.status_code == 410
    assert code(res) == "INVITATION_EXPIRED"


async def test_revoke_project_invitation(client: AsyncClient) -> None:
    """PRJ 명세 §4.9 — 초대 취소. 취소된 초대는 수락할 수 없다."""
    owner = await signup(client, "prev@example.com")
    guest = await signup(client, "prevguest@example.com")
    project = await create_project(client, owner["headers"])

    invitation = (
        await client.post(
            f"/projects/{project['project_id']}/invitations",
            json={"invited_email": guest["email"], "role": "editor"},
            headers=owner["headers"],
        )
    ).json()["data"]

    revoked = await client.delete(
        f"/projects/{project['project_id']}/invitations/{invitation['invitation_id']}",
        headers=owner["headers"],
    )
    assert revoked.status_code == 204

    dead = await client.post(
        f"/projects/{project['project_id']}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=guest["headers"],
    )
    assert code(dead) == "INVITATION_NOT_PENDING"

    unknown = await client.delete(
        f"/projects/{project['project_id']}/invitations/00000000-0000-0000-0000-000000000000",
        headers=owner["headers"],
    )
    assert code(unknown) == "INVITATION_NOT_FOUND"


async def test_manuscript_count_is_aggregated(client: AsyncClient, db: AsyncSession) -> None:
    """PRJ 명세 §2.1 의 manuscript_count. content(Ring 2) 를 조합 레이어가 세어 붙인다."""
    from app.content.manuscripts.models import Manuscript

    user = await signup(client, "mc@example.com")
    project = await create_project(client, user["headers"])
    assert project["manuscript_count"] == 0

    db.add(
        Manuscript(
            project_id=UUID(project["project_id"]),
            title="원고",
            source_type="editor",
            content="",
            status="draft",
        )
    )
    await db.commit()

    detail = await client.get(f"/projects/{project['project_id']}", headers=user["headers"])
    assert detail.json()["data"]["manuscript_count"] == 1

    listed = await client.get("/projects", headers=user["headers"])
    assert [p["manuscript_count"] for p in listed.json()["data"]] == [1]

    # 수정 응답도 같은 필드 집합을 유지한다 (엔드포인트마다 달라지지 않는다)
    patched = await client.patch(
        f"/projects/{project['project_id']}",
        json={"title": "고침"},
        headers=user["headers"],
    )
    assert patched.json()["data"]["manuscript_count"] == 1


async def test_wrong_token_is_404_whatever_the_invitation_state(client: AsyncClient) -> None:
    """토큰을 상태보다 먼저 본다 — 취소된 초대도 틀린 토큰에는 404 다."""
    owner = await signup(client, "pws@example.com")
    guest = await signup(client, "pwsguest@example.com")
    project = await create_project(client, owner["headers"])
    pid = project["project_id"]
    invitation = (
        await client.post(
            f"/projects/{pid}/invitations",
            json={"invited_email": guest["email"]},
            headers=owner["headers"],
        )
    ).json()["data"]
    revoked = await client.delete(
        f"/projects/{pid}/invitations/{invitation['invitation_id']}", headers=owner["headers"]
    )
    assert revoked.status_code == 204

    url = f"/projects/{pid}/invitations/{invitation['invitation_id']}/accept"
    wrong = await client.post(url, json={"token": "wrong"}, headers=guest["headers"])
    assert (wrong.status_code, code(wrong)) == (404, "INVITATION_NOT_FOUND")
    right = await client.post(url, json={"token": invitation["token"]}, headers=guest["headers"])
    assert code(right) == "INVITATION_NOT_PENDING"
