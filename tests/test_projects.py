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


async def test_team_project_requires_team_admin(client: AsyncClient) -> None:
    owner = await signup(client, "towner@example.com")
    member = await signup(client, "tmember@example.com")
    team_id = (await client.post("/teams", json={"name": "팀"}, headers=owner["headers"])).json()[
        "data"
    ]["id"]

    invitation = (
        await client.post(
            f"/teams/{team_id}/invitations",
            json={"email": "tmember@example.com", "role": "member"},
            headers=owner["headers"],
        )
    ).json()["data"]
    await client.post(
        f"/teams/{team_id}/invitations/{invitation['id']}/accept", headers=member["headers"]
    )

    project = await create_project(
        client, owner["headers"], owner_type="team", team_id=team_id, name="팀 프로젝트"
    )
    assert project["team_id"] == team_id

    # role=member 는 팀 프로젝트를 만들 수 없다.
    denied = await client.post(
        "/projects",
        json={"name": "몰래", "owner_type": "team", "team_id": team_id},
        headers=member["headers"],
    )
    assert denied.status_code == 403

    outsider = await signup(client, "nope@example.com")
    stranger = await client.post(
        "/projects",
        json={"name": "몰래", "owner_type": "team", "team_id": team_id},
        headers=outsider["headers"],
    )
    assert stranger.status_code == 403


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
