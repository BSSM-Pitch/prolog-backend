from httpx import AsyncClient

from tests.conftest import code, signup


async def test_signup_returns_user_and_tokens(client: AsyncClient) -> None:
    res = await client.post(
        "/auth/signup",
        json={
            "email": "a@example.com",
            "password": "hunter2!pw",
            "nickname": "a",
            "role": "writer",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["data"]["user"]["email"] == "a@example.com"
    assert body["data"]["token"]["token_type"] == "Bearer"
    # users.plan 은 응답에 노출하지 않는다 (CLAUDE.md §5)
    assert "plan" not in body["data"]["user"]
    assert body["meta"] == {}


async def test_signup_duplicate_email_is_409(client: AsyncClient) -> None:
    await signup(client, "dup@example.com")
    res = await client.post(
        "/auth/signup",
        json={"email": "DUP@example.com", "password": "hunter2!pw", "nickname": "d"},
    )
    assert res.status_code == 409
    assert code(res) == "EMAIL_ALREADY_EXISTS"


async def test_signup_short_password_is_422(client: AsyncClient) -> None:
    res = await client.post(
        "/auth/signup", json={"email": "x@example.com", "password": "short", "nickname": "x"}
    )
    assert res.status_code == 422
    assert code(res) == "VALIDATION_ERROR"


async def test_login_and_wrong_password(client: AsyncClient) -> None:
    await signup(client, "login@example.com")
    res = await client.post(
        "/auth/login", json={"email": "login@example.com", "password": "hunter2!pw"}
    )
    assert res.status_code == 200
    assert res.json()["data"]["token"]["access_token"]

    bad = await client.post(
        "/auth/login", json={"email": "login@example.com", "password": "nope!pass"}
    )
    assert bad.status_code == 401
    assert code(bad) == "INVALID_CREDENTIALS"


async def test_login_unknown_email_is_401(client: AsyncClient) -> None:
    res = await client.post(
        "/auth/login", json={"email": "ghost@example.com", "password": "hunter2!pw"}
    )
    assert code(res) == "INVALID_CREDENTIALS"


async def test_me_requires_token(client: AsyncClient) -> None:
    user = await signup(client, "me@example.com")
    res = await client.get("/auth/me", headers=user["headers"])
    assert res.status_code == 200
    assert res.json()["data"]["id"] == user["id"]

    anon = await client.get("/auth/me")
    assert anon.status_code == 401
    assert code(anon) == "UNAUTHORIZED"

    bogus = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bogus.status_code == 401


async def test_refresh_rotates_and_old_token_dies(client: AsyncClient) -> None:
    user = await signup(client, "rot@example.com")
    first = await client.post("/auth/refresh", json={"refresh_token": user["refresh_token"]})
    assert first.status_code == 200
    new_token = first.json()["data"]["refresh_token"]
    assert new_token != user["refresh_token"]

    replay = await client.post("/auth/refresh", json={"refresh_token": user["refresh_token"]})
    assert replay.status_code == 401
    assert code(replay) == "INVALID_REFRESH_TOKEN"

    assert (
        await client.post("/auth/refresh", json={"refresh_token": new_token})
    ).status_code == 200


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    user = await signup(client, "out@example.com")
    res = await client.post("/auth/logout", json={"refresh_token": user["refresh_token"]})
    assert res.status_code == 204
    dead = await client.post("/auth/refresh", json={"refresh_token": user["refresh_token"]})
    assert code(dead) == "INVALID_REFRESH_TOKEN"
