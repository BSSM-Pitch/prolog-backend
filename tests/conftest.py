import os
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionFactory, engine
from app.main import app
from app.platform_.auth.google import GoogleIdentity, google_oauth

SYNC_URL = settings.database_url.replace("+asyncpg", "")
SCHEMAS = ("platform", "content", "authoring", "insight", "ops")


def pytest_configure(config: pytest.Config) -> None:
    """테스트는 `_test` DB 에서만 돈다 (CLAUDE.md §2 규칙 2). 제거 금지."""
    url = os.environ.get("DATABASE_URL")
    if url is None or not url.rsplit("/", 1)[-1].partition("?")[0].endswith("_test"):
        pytest.exit(
            f"DATABASE_URL 이 '_test' 로 끝나는 DB 가 아니다 (got: {url!r}). "
            "dev DB 를 지우지 않기 위해 중단한다.",
            returncode=2,
        )


def _ensure_database() -> None:
    admin_url, _, dbname = SYNC_URL.rpartition("/")
    with psycopg.connect(f"{admin_url}/postgres", autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{dbname}"')


@pytest.fixture(scope="session", autouse=True)
def migrate() -> Iterator[None]:
    _ensure_database()
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema = ANY(:schemas) AND table_type = 'BASE TABLE'"
            ),
            {"schemas": list(SCHEMAS)},
        )
        tables = ", ".join(f'"{s}"."{t}"' for s, t in rows)
        if tables:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test/v1") as c:
        yield c


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


class FakeGoogle:
    """Google 포트의 테스트 구현.

    ``oauth_code`` 를 ``"{sub}|{email}"`` 로 읽는다. 실제 프로덕션 경로(2단계 가입)를
    그대로 통과시키기 위한 것이다 — 로컬 가입 같은 우회 엔드포인트를 만들지 않는다
    (CLAUDE.md §4.4).
    """

    async def exchange(self, oauth_code: str) -> GoogleIdentity:
        sub, _, email = oauth_code.partition("|")
        return GoogleIdentity(sub=sub, email=email or None)


app.dependency_overrides[google_oauth] = FakeGoogle


def oauth_code(email: str) -> str:
    return f"sub-{email}|{email}"


async def google_ticket(client: AsyncClient, email: str) -> str:
    res = await client.post("/auth/oauth/google", json={"oauth_code": oauth_code(email)})
    assert res.status_code == 200, res.text
    assert res.json()["meta"]["is_new_user"] is True
    return str(res.json()["data"]["signup_ticket"])


async def signup(
    client: AsyncClient, email: str, username: str | None = None, role: str = "writer"
) -> dict:
    """Google 인증 → 가입 완료. 2단계를 모두 태운다."""
    ticket = await google_ticket(client, email)
    res = await client.post(
        "/auth/signup",
        json={"signup_ticket": ticket, "username": username or email.split("@")[0], "role": role},
    )
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    return {
        "id": data["user"]["user_id"],
        "email": data["user"]["email"],
        "username": data["user"]["username"],
        "signup_ticket": ticket,
        "headers": {"Authorization": f"Bearer {data['tokens']['access_token']}"},
        "refresh_token": data["tokens"]["refresh_token"],
    }


def code(res) -> str:  # type: ignore[no-untyped-def]
    return res.json()["error"]["code"]
