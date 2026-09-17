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


async def signup(client: AsyncClient, email: str, password: str = "hunter2!pw") -> dict:
    res = await client.post(
        "/auth/signup",
        json={"email": email, "password": password, "nickname": email.split("@")[0]},
    )
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    return {
        "id": data["user"]["id"],
        "email": data["user"]["email"],
        "headers": {"Authorization": f"Bearer {data['token']['access_token']}"},
        "refresh_token": data["token"]["refresh_token"],
    }


def code(res) -> str:  # type: ignore[no-untyped-def]
    return res.json()["error"]["code"]
