"""AUTH v0.2 — Google 단일, 2단계 가입."""

from httpx import AsyncClient

from tests.conftest import code, google_ticket, oauth_code, signup


async def test_oauth_new_user_gets_ticket_without_creating_account(client: AsyncClient) -> None:
    res = await client.post("/auth/oauth/google", json={"oauth_code": oauth_code("new@ex.com")})
    assert res.status_code == 200
    body = res.json()
    assert body["meta"]["is_new_user"] is True
    assert body["data"]["email"] == "new@ex.com"
    assert body["data"]["signup_ticket"]
    # 계정은 아직 없다 — 티켓만 받은 상태에서 아이디는 비어 있어야 한다.
    check = await client.get("/users/check-username?username=new")
    assert check.json()["data"]["available"] is True


async def test_signup_completes_and_returns_user_and_tokens(client: AsyncClient) -> None:
    ticket = await google_ticket(client, "done@ex.com")
    res = await client.post(
        "/auth/signup", json={"signup_ticket": ticket, "username": "done", "role": "reader"}
    )
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["user"]["username"] == "done"
    assert data["user"]["email"] == "done@ex.com"
    assert data["user"]["role"] == "reader"
    assert data["user"]["auth_provider"] == "google"
    assert data["tokens"]["token_type"] == "Bearer"
    # plan 과 provider_user_id 는 응답에 노출하지 않는다.
    assert "plan" not in data["user"]
    assert "provider_user_id" not in data["user"]


async def test_second_oauth_is_login_not_signup(client: AsyncClient) -> None:
    user = await signup(client, "again@ex.com")
    res = await client.post("/auth/oauth/google", json={"oauth_code": oauth_code("again@ex.com")})
    assert res.status_code == 200
    assert res.json()["meta"]["is_new_user"] is False
    assert res.json()["data"]["user"]["user_id"] == user["id"]


async def test_signup_without_username_is_username_required(client: AsyncClient) -> None:
    ticket = await google_ticket(client, "nouser@ex.com")
    res = await client.post("/auth/signup", json={"signup_ticket": ticket, "role": "writer"})
    assert res.status_code == 400
    assert code(res) == "USERNAME_REQUIRED"


async def test_signup_duplicate_username_is_409(client: AsyncClient) -> None:
    await signup(client, "first@ex.com", username="taken")
    ticket = await google_ticket(client, "second@ex.com")
    res = await client.post(
        "/auth/signup", json={"signup_ticket": ticket, "username": "taken", "role": "writer"}
    )
    assert res.status_code == 409
    assert code(res) == "USERNAME_TAKEN"


async def test_forged_signup_ticket_is_401(client: AsyncClient) -> None:
    res = await client.post(
        "/auth/signup", json={"signup_ticket": "not-a-jwt", "username": "x", "role": "writer"}
    )
    assert res.status_code == 401
    assert code(res) == "SIGNUP_TICKET_INVALID"


# --- CLAUDE.md §9 필수 3종 ------------------------------------------------


async def test_signup_ticket_is_rejected_as_access_token(client: AsyncClient) -> None:
    """가입 티켓을 Authorization 으로 제출하면 401. 같은 키로 서명되므로 aud 로 갈린다."""
    ticket = await google_ticket(client, "ticket@ex.com")
    res = await client.get("/users/me", headers={"Authorization": f"Bearer {ticket}"})
    assert res.status_code == 401
    assert code(res) == "UNAUTHORIZED"


async def test_refresh_rotates_and_reuse_is_rejected(client: AsyncClient) -> None:
    user = await signup(client, "rot@ex.com")
    first = await client.post("/auth/token/refresh", json={"refresh_token": user["refresh_token"]})
    assert first.status_code == 200
    rotated = first.json()["data"]["refresh_token"]
    assert rotated != user["refresh_token"]

    replay = await client.post("/auth/token/refresh", json={"refresh_token": user["refresh_token"]})
    assert replay.status_code == 401
    assert code(replay) == "REFRESH_TOKEN_INVALID"

    alive = await client.post("/auth/token/refresh", json={"refresh_token": rotated})
    assert alive.status_code == 200


async def test_tenant_isolation_between_users(client: AsyncClient) -> None:
    """A 의 팀·프로젝트를 B 가 읽지 못한다."""
    a = await signup(client, "a@ex.com")
    b = await signup(client, "b@ex.com")
    team_id = (await client.post("/teams", json={"name": "A 팀"}, headers=a["headers"])).json()[
        "data"
    ]["team_id"]
    project_id = (
        await client.post(
            "/projects",
            json={"title": "A 프로젝트", "owner_type": "personal"},
            headers=a["headers"],
        )
    ).json()["data"]["project_id"]

    assert (await client.get(f"/teams/{team_id}", headers=b["headers"])).status_code == 403
    assert (await client.get(f"/projects/{project_id}", headers=b["headers"])).status_code == 403
    assert (await client.get("/teams", headers=b["headers"])).json()["data"] == []
    assert (await client.get("/projects", headers=b["headers"])).json()["data"] == []


# --- /users ---------------------------------------------------------------


async def test_me_requires_access_token(client: AsyncClient) -> None:
    user = await signup(client, "me@ex.com")
    res = await client.get("/users/me", headers=user["headers"])
    assert res.status_code == 200
    assert res.json()["data"]["user_id"] == user["id"]

    assert (await client.get("/users/me")).status_code == 401
    bogus = await client.get("/users/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bogus.status_code == 401
    assert code(bogus) == "UNAUTHORIZED"


async def test_patch_me_changes_role_only(client: AsyncClient) -> None:
    user = await signup(client, "role@ex.com", role="reader")
    res = await client.patch("/users/me", json={"role": "writer"}, headers=user["headers"])
    assert res.status_code == 200
    assert res.json()["data"]["role"] == "writer"

    bad = await client.patch("/users/me", json={"role": "admin"}, headers=user["headers"])
    assert bad.status_code == 400
    assert code(bad) == "INVALID_INPUT"


async def test_check_username_needs_no_auth(client: AsyncClient) -> None:
    await signup(client, "dup@ex.com", username="dup")
    taken = await client.get("/users/check-username?username=dup")
    assert taken.status_code == 200
    assert taken.json()["data"] == {"username": "dup", "available": False}
    assert (await client.get("/users/check-username?username=free")).json()["data"][
        "available"
    ] is True


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    user = await signup(client, "out@ex.com")
    res = await client.post("/auth/logout", json={"refresh_token": user["refresh_token"]})
    assert res.status_code == 204
    dead = await client.post("/auth/token/refresh", json={"refresh_token": user["refresh_token"]})
    assert code(dead) == "REFRESH_TOKEN_INVALID"
