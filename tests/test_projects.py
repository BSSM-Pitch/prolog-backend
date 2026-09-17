from httpx import AsyncClient

from tests.conftest import code, signup


async def create_project(client: AsyncClient, headers: dict, **kwargs: object) -> dict:
    body = {"name": "프로젝트", "owner_type": "personal"} | kwargs
    res = await client.post("/projects", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def test_create_personal_project(client: AsyncClient) -> None:
    user = await signup(client, "p1@example.com")
    project = await create_project(client, user["headers"])
    assert project["owner_type"] == "personal"
    assert project["team_id"] is None

    members = await client.get(f"/projects/{project['id']}/members", headers=user["headers"])
    assert members.json()["data"][0] == {
        "user_id": user["id"],
        "role": "owner",
        "joined_at": members.json()["data"][0]["joined_at"],
    }


async def test_owner_type_and_team_id_must_agree(client: AsyncClient) -> None:
    user = await signup(client, "p2@example.com")
    res = await client.post(
        "/projects", json={"name": "x", "owner_type": "team"}, headers=user["headers"]
    )
    assert res.status_code == 400
    assert code(res) == "INVALID_OWNER_TYPE"


async def join_team(client: AsyncClient, owner: dict, guest: dict, team_id: str, role: str) -> None:
    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"email": guest["email"], "role": role},
            headers=owner["headers"],
        )
    ).json()["data"]
    res = await client.post(
        f"/teams/{team_id}/invitations/{invitation['id']}/accept", headers=guest["headers"]
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
    ]["id"]
    await join_team(client, owner, member, team_id, "member")

    project = await create_project(
        client, member["headers"], owner_type="team", team_id=team_id, name="팀원이 만든다"
    )
    assert project["team_id"] == team_id

    # 비소속자는 일반 FORBIDDEN 이 아니라 NOT_TEAM_MEMBER 다 (§6.1).
    stranger = await client.post(
        "/projects",
        json={"name": "몰래", "owner_type": "team", "team_id": team_id},
        headers=outsider["headers"],
    )
    assert stranger.status_code == 403
    assert code(stranger) == "NOT_TEAM_MEMBER"

    # 없는 팀이면 TEAM_NOT_FOUND (PRJ 명세 §1.4).
    ghost = await client.post(
        "/projects",
        json={
            "name": "유령",
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
    ]["id"]
    await join_team(client, owner, admin, team_id, "admin")
    await join_team(client, owner, member, team_id, "member")

    # [1] 일반 팀원도 만든다  [2] admin 도 만든다
    await create_project(client, member["headers"], owner_type="team", team_id=team_id, name="M")
    project = await create_project(
        client, admin["headers"], owner_type="team", team_id=team_id, name="A"
    )
    pid = project["id"]

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
        json={"email": "x@example.com", "role": "viewer"},
        headers=member["headers"],
    )
    assert invited.status_code == 403

    # [6][7] 목록에는 아직 안 뜬다 — GET /projects 가 project_members 만 조인한다.
    # 감사 A표 P2(팀 프로젝트 포함) 미결이라 현재 동작을 그대로 고정해 둔다.
    assert [
        p["name"] for p in (await client.get("/projects", headers=owner["headers"])).json()["data"]
    ] == []
    assert [
        p["name"] for p in (await client.get("/projects", headers=member["headers"])).json()["data"]
    ] == ["M"]

    # [8] 비소속자는 생성도 조회도 막힌다
    assert (await client.get(f"/projects/{pid}", headers=outsider["headers"])).status_code == 403
    denied = await client.post(
        "/projects",
        json={"name": "몰래", "owner_type": "team", "team_id": team_id},
        headers=outsider["headers"],
    )
    assert code(denied) == "NOT_TEAM_MEMBER"

    # 개인 프로젝트는 팀 해석의 영향을 받지 않는다
    personal = await create_project(client, owner["headers"], name="개인")
    assert (
        await client.get(f"/projects/{personal['id']}", headers=member["headers"])
    ).status_code == 403


async def test_project_role_hierarchy(client: AsyncClient) -> None:
    owner = await signup(client, "prole@example.com")
    viewer = await signup(client, "pviewer@example.com")
    project = await create_project(client, owner["headers"])

    invitation = (
        await client.post(
            f"/projects/{project['id']}/invitations",
            json={"email": "pviewer@example.com", "role": "viewer"},
            headers=owner["headers"],
        )
    ).json()["data"]
    await client.post(
        f"/projects/{project['id']}/invitations/{invitation['id']}/accept",
        headers=viewer["headers"],
    )

    assert (
        await client.get(f"/projects/{project['id']}", headers=viewer["headers"])
    ).status_code == 200
    # viewer < editor → PATCH 금지
    denied = await client.patch(
        f"/projects/{project['id']}", json={"name": "고침"}, headers=viewer["headers"]
    )
    assert denied.status_code == 403
    # viewer < owner → 초대 목록 금지
    assert (
        await client.get(f"/projects/{project['id']}/invitations", headers=viewer["headers"])
    ).status_code == 403

    patched = await client.patch(
        f"/projects/{project['id']}", json={"name": "고침"}, headers=owner["headers"]
    )
    assert patched.json()["data"]["name"] == "고침"


async def test_project_not_found_and_forbidden(client: AsyncClient) -> None:
    owner = await signup(client, "pnf@example.com")
    stranger = await signup(client, "pstranger@example.com")
    project = await create_project(client, owner["headers"])

    missing = await client.get(
        "/projects/00000000-0000-0000-0000-000000000000", headers=owner["headers"]
    )
    assert missing.status_code == 404
    assert code(missing) == "PROJECT_NOT_FOUND"

    denied = await client.get(f"/projects/{project['id']}", headers=stranger["headers"])
    assert denied.status_code == 403
    assert code(denied) == "FORBIDDEN"


async def test_member_role_update_and_removal(client: AsyncClient) -> None:
    owner = await signup(client, "pm@example.com")
    mate = await signup(client, "pmate@example.com")
    project = await create_project(client, owner["headers"])
    invitation = (
        await client.post(
            f"/projects/{project['id']}/invitations",
            json={"email": "pmate@example.com", "role": "editor"},
            headers=owner["headers"],
        )
    ).json()["data"]
    await client.post(
        f"/projects/{project['id']}/invitations/{invitation['id']}/accept", headers=mate["headers"]
    )

    promoted = await client.patch(
        f"/projects/{project['id']}/members/{mate['id']}",
        json={"role": "owner"},
        headers=owner["headers"],
    )
    assert promoted.json()["data"]["role"] == "owner"

    assert (
        await client.delete(
            f"/projects/{project['id']}/members/{mate['id']}", headers=owner["headers"]
        )
    ).status_code == 204

    last = await client.delete(
        f"/projects/{project['id']}/members/{owner['id']}", headers=owner["headers"]
    )
    assert code(last) == "LAST_OWNER_CANNOT_LEAVE"


async def test_project_delete_cascades_membership(client: AsyncClient) -> None:
    owner = await signup(client, "pdel@example.com")
    project = await create_project(client, owner["headers"])
    assert (
        await client.delete(f"/projects/{project['id']}", headers=owner["headers"])
    ).status_code == 204
    assert (await client.get("/projects", headers=owner["headers"])).json()["data"] == []


async def test_phase0_done_criteria(client: AsyncClient) -> None:
    """인증된 사용자가 프로젝트를 만들고 동료를 초대해 수락까지 마친다 (CLAUDE.md §13)."""
    creator = await signup(client, "creator@example.com")
    colleague = await signup(client, "colleague@example.com")

    project = await create_project(client, creator["headers"], name="장편 소설")
    invitation = (
        await client.post(
            f"/projects/{project['id']}/invitations",
            json={"email": "colleague@example.com", "role": "editor"},
            headers=creator["headers"],
        )
    ).json()["data"]
    accepted = await client.post(
        f"/projects/{project['id']}/invitations/{invitation['id']}/accept",
        headers=colleague["headers"],
    )
    assert accepted.status_code == 200

    mine = await client.get("/projects", headers=colleague["headers"])
    assert [p["id"] for p in mine.json()["data"]] == [project["id"]]
    edited = await client.patch(
        f"/projects/{project['id']}",
        json={"description": "동료가 고친다"},
        headers=colleague["headers"],
    )
    assert edited.json()["data"]["description"] == "동료가 고친다"
