from httpx import AsyncClient

from tests.conftest import code, signup
from tests.test_projects import create_project
from tests.test_teams import create_team


async def test_received_invitations_and_accept_without_token(client: AsyncClient) -> None:
    """내게 온 초대 목록에서 토큰 없이 수락한다. 권한은 초대받은 이메일 = 로그인 이메일이다."""
    owner = await signup(client, "rcv-owner@example.com")
    guest = await signup(client, "rcv-guest@example.com")
    other = await signup(client, "rcv-other@example.com")
    team_id = await create_team(client, owner["headers"], name="받은팀")
    project = await create_project(client, owner["headers"], title="받은프로젝트")

    team_inv = (
        await client.post(
            f"/teams/{team_id}/invitations",
            # 대소문자가 달라도 같은 주소다
            json={"invited_email": "RCV-Guest@example.com", "role": "member"},
            headers=owner["headers"],
        )
    ).json()["data"]
    project_inv = (
        await client.post(
            f"/projects/{project['project_id']}/invitations",
            json={"invited_email": guest["email"], "role": "editor"},
            headers=owner["headers"],
        )
    ).json()["data"]

    listed = await client.get("/users/me/invitations", headers=guest["headers"])
    assert listed.status_code == 200
    rows = listed.json()["data"]
    assert [(r["target_type"], r["target_name"]) for r in rows] == [
        ("project", "받은프로젝트"),
        ("team", "받은팀"),
    ]
    assert "token" not in rows[0]
    assert (await client.get("/users/me/invitations", headers=other["headers"])).json()[
        "data"
    ] == []

    # 다른 주소의 사용자는 토큰 없이 수락하지 못한다 — 없는 초대와 같은 404
    stolen = await client.post(
        f"/teams/{team_id}/invitations/{team_inv['invitation_id']}/accept",
        json={},
        headers=other["headers"],
    )
    assert stolen.status_code == 404
    assert code(stolen) == "TEAM_INVITATION_NOT_FOUND"
    stolen = await client.post(
        f"/projects/{project['project_id']}/invitations/{project_inv['invitation_id']}/accept",
        json={},
        headers=other["headers"],
    )
    assert code(stolen) == "INVITATION_NOT_FOUND"

    for path in (
        f"/teams/{team_id}/invitations/{team_inv['invitation_id']}/accept",
        f"/projects/{project['project_id']}/invitations/{project_inv['invitation_id']}/accept",
    ):
        res = await client.post(path, json={}, headers=guest["headers"])
        assert res.status_code == 200, res.text

    # 수락한 초대는 목록에서 빠진다
    assert (await client.get("/users/me/invitations", headers=guest["headers"])).json()[
        "data"
    ] == []
