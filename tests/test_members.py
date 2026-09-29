"""멤버 목록 — 커서 페이지네이션(§6.6)과 팀 프로젝트의 팀 유래 멤버."""

from httpx import AsyncClient

from tests.conftest import code, signup
from tests.test_projects import create_project, join_team
from tests.test_teams import create_team


async def _pages(client: AsyncClient, url: str, headers: dict) -> list[list[dict]]:
    """limit=1 로 끝까지 넘긴다. 페이지마다 data 를 모은다."""
    pages, cursor = [], None
    while True:
        params = {"limit": 1} | ({"cursor": cursor} if cursor else {})
        res = await client.get(url, params=params, headers=headers)
        assert res.status_code == 200, res.text
        pages.append(res.json()["data"])
        cursor = res.json()["meta"]["next_cursor"]
        if cursor is None:
            return pages


async def test_team_members_are_cursor_paginated(client: AsyncClient) -> None:
    owner = await signup(client, "tm-owner@example.com")
    a = await signup(client, "tm-a@example.com")
    b = await signup(client, "tm-b@example.com")
    team_id = await create_team(client, owner["headers"])
    await join_team(client, owner, a, team_id, "member")
    await join_team(client, owner, b, team_id, "admin")

    pages = await _pages(client, f"/teams/{team_id}/members", owner["headers"])
    # 한 페이지에 하나씩, 가입 순서대로, 빠짐·중복 없이
    assert [[m["user_id"] for m in p] for p in pages] == [[owner["id"]], [a["id"]], [b["id"]]]
    assert all(set(p[0]) >= {"username", "role", "joined_at"} for p in pages)

    bad = await client.get(
        f"/teams/{team_id}/members", params={"cursor": "!!"}, headers=owner["headers"]
    )
    assert bad.status_code == 400
    assert code(bad) == "INVALID_INPUT"


async def test_team_project_members_include_team_members(client: AsyncClient) -> None:
    """팀원은 project_members 행 없이도 editor 로 작업한다(P0). 목록이 이를 숨기면 안 된다."""
    owner = await signup(client, "tpm-owner@example.com")
    mate = await signup(client, "tpm-mate@example.com")
    team_id = await create_team(client, owner["headers"])
    await join_team(client, owner, mate, team_id, "member")
    project = await create_project(client, owner["headers"], owner_type="team", team_id=team_id)
    pid = project["project_id"]

    listed = await client.get(f"/projects/{pid}/members", headers=mate["headers"])
    assert listed.status_code == 200
    by_user = {m["user_id"]: m for m in listed.json()["data"]}
    # 생성자는 명시적 owner 이고 팀 owner 이기도 하다 — 한 번만, 더 높은 명시적 role 로.
    assert (by_user[owner["id"]]["role"], by_user[owner["id"]]["source"]) == ("owner", "project")
    assert (by_user[mate["id"]]["role"], by_user[mate["id"]]["source"]) == ("editor", "team")
    assert by_user[mate["id"]]["username"] == mate["username"]
    assert len(listed.json()["data"]) == 2

    # 목록의 role 이 실제 권한과 같다: 팀원은 editor 로 수정할 수 있다.
    edited = await client.patch(
        f"/projects/{pid}", json={"title": "팀원이 고침"}, headers=mate["headers"]
    )
    assert edited.status_code == 200

    # 팀에서 나가면 목록에서도 사라진다.
    left = await client.delete(f"/teams/{team_id}/members/{mate['id']}", headers=mate["headers"])
    assert left.status_code == 204
    after = await client.get(f"/projects/{pid}/members", headers=owner["headers"])
    assert [m["user_id"] for m in after.json()["data"]] == [owner["id"]]


async def test_team_grant_wins_over_lower_explicit_role(client: AsyncClient) -> None:
    """명시적 viewer 이면서 팀원이면 실제 권한은 editor 다(core.deps.effective_project_role)."""
    owner = await signup(client, "tg-owner@example.com")
    guest = await signup(client, "tg-guest@example.com")
    team_id = await create_team(client, owner["headers"])
    project = await create_project(client, owner["headers"], owner_type="team", team_id=team_id)
    pid = project["project_id"]

    invitation = (
        await client.post(
            f"/projects/{pid}/invitations",
            json={"invited_email": guest["email"], "role": "viewer"},
            headers=owner["headers"],
        )
    ).json()["data"]
    accepted = await client.post(
        f"/projects/{pid}/invitations/{invitation['invitation_id']}/accept",
        json={"token": invitation["token"]},
        headers=guest["headers"],
    )
    assert accepted.status_code == 200
    await join_team(client, owner, guest, team_id, "member")

    rows = [
        m
        for m in (await client.get(f"/projects/{pid}/members", headers=owner["headers"])).json()[
            "data"
        ]
        if m["user_id"] == guest["id"]
    ]
    assert [(m["role"], m["source"]) for m in rows] == [("editor", "team")]


async def test_project_members_are_cursor_paginated_across_sources(client: AsyncClient) -> None:
    owner = await signup(client, "pp-owner@example.com")
    mates = [await signup(client, f"pp-{i}@example.com") for i in range(3)]
    team_id = await create_team(client, owner["headers"])
    for mate in mates:
        await join_team(client, owner, mate, team_id, "member")
    project = await create_project(client, owner["headers"], owner_type="team", team_id=team_id)

    pages = await _pages(client, f"/projects/{project['project_id']}/members", owner["headers"])
    assert [len(p) for p in pages] == [1, 1, 1, 1]
    # joined_at 순. 팀원의 joined_at 은 팀 가입 시각이고, owner 는 명시적 행(프로젝트 생성
    # 시각)으로 나온다 — 팀원들보다 나중이라 마지막이다.
    assert [p[0]["user_id"] for p in pages] == [*[m["id"] for m in mates], owner["id"]]
