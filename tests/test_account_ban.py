import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core import errors
from app.db.session import SessionFactory
from app.platform_.auth import service
from app.platform_.auth.models import RefreshToken, User
from app.platform_.auth.moderation import ban_account
from tests.conftest import code, oauth_code, signup


async def ban(uid):
    async with SessionFactory() as session:
        user = await ban_account(session, UUID(uid), reason="운영자 요청")
        at = user.banned_at
        await session.commit()
        return at


async def test_ban_blocks_existing_access_and_refresh_without_deleting_content(client):
    user = await signup(client, "banned@example.com")
    other = await signup(client, "active@example.com")
    project = (
        await client.post(
            "/projects",
            headers=user["headers"],
            json={"title": "보존할 작품", "owner_type": "personal"},
        )
    ).json()["data"]["project_id"]
    first = await ban(user["id"])
    assert await ban(user["id"]) == first
    for path in ["/users/me", "/projects", f"/projects/{project}", "/teams"]:
        response = await client.get(path, headers=user["headers"])
        assert response.status_code == 403 and code(response) == "ACCOUNT_BANNED"
    refresh = await client.post(
        "/auth/token/refresh", json={"refresh_token": user["refresh_token"]}
    )
    assert refresh.status_code == 401 and code(refresh) == "REFRESH_TOKEN_INVALID"
    assert (await client.get("/users/me", headers=other["headers"])).status_code == 200
    async with SessionFactory() as session:
        from app.platform_.projects.models import Project

        saved = await session.get(User, UUID(user["id"]))
        assert saved and saved.username == user["username"] and saved.ban_reason == "운영자 요청"
        assert (await session.get(Project, UUID(project))).title == "보존할 작품"
        tokens = (
            await session.scalars(select(RefreshToken).where(RefreshToken.user_id == saved.id))
        ).all()
        assert tokens and all(t.revoked_at for t in tokens)


@pytest.mark.parametrize("provider", ["google", "naver"])
async def test_banned_social_accounts_cannot_login_again(client, provider):
    body = {"oauth_code": oauth_code(f"{provider}@example.com")}
    if provider == "naver":
        body["state"] = "test"
    path = f"/auth/oauth/{provider}"
    ticket = (await client.post(path, json=body)).json()["data"]["signup_ticket"]
    made = await client.post(
        "/auth/signup", json={"signup_ticket": ticket, "username": provider, "role": "writer"}
    )
    assert made.status_code == 201
    await ban(made.json()["data"]["user"]["user_id"])
    denied = await client.post(path, json=body)
    assert denied.status_code == 403 and code(denied) == "ACCOUNT_BANNED"


async def test_banned_local_login_and_unrevoked_refresh_are_denied(client):
    made = await client.post(
        "/auth/signup",
        json={
            "username": "localban",
            "email": "localban@example.com",
            "password": "password123",
            "role": "writer",
        },
    )
    assert made.status_code == 201
    data = made.json()["data"]
    async with SessionFactory() as session:
        from datetime import UTC, datetime

        user = await session.get(User, UUID(data["user"]["user_id"]))
        user.banned_at = datetime.now(UTC)
        await session.commit()
    login = await client.post(
        "/auth/login", json={"login_id": "localban@example.com", "password": "password123"}
    )
    assert login.status_code == 403 and code(login) == "ACCOUNT_BANNED"
    refresh = await client.post(
        "/auth/token/refresh", json={"refresh_token": data["tokens"]["refresh_token"]}
    )
    assert refresh.status_code == 403 and code(refresh) == "ACCOUNT_BANNED"


async def test_session_issuance_waits_for_concurrent_ban_and_rechecks_stale_user(client):
    user = await signup(client, "raceban@example.com")
    async with SessionFactory() as issuing, SessionFactory() as moderating:
        stale = await issuing.get(User, UUID(user["id"]))
        assert stale.banned_at is None
        await ban_account(moderating, stale.id, reason="운영자 요청")
        started = asyncio.Event()

        async def issue():
            started.set()
            return await service._issue(issuing, stale)

        task = asyncio.create_task(issue())
        await started.wait()
        await moderating.commit()
        with pytest.raises(errors.AccountBanned):
            await asyncio.wait_for(task, timeout=5)
        await issuing.rollback()


async def test_refresh_rotation_is_single_use_under_concurrent_requests(client):
    user = await signup(client, "rotation-race@example.com")
    results = await asyncio.gather(
        *[
            client.post("/auth/token/refresh", json={"refresh_token": user["refresh_token"]})
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in results) == [200, 401]
    assert code(next(r for r in results if r.status_code == 401)) == "REFRESH_TOKEN_INVALID"
