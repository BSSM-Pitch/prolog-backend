import json
import os
import re
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, ClassVar

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import ai_client
from app.content.manuscripts.storage import PresignedUpload, storage
from app.core.config import settings
from app.core.errors import AppError
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


def _empty_tables() -> None:
    """downgrade 전에 지난 실행이 남긴 행을 비운다(clean_tables 는 테스트 **시작** 에 비운다).

    downgrade 는 구조를 되돌린다 — 행이 있으면 NOT NULL 복원 같은 곳에서 멈춘다.
    `pytest_configure` 가드가 이 DB 가 `_test` 임을 이미 확인했다.
    """
    with psycopg.connect(SYNC_URL, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema = ANY(%s) AND table_type = 'BASE TABLE'",
            (list(SCHEMAS),),
        ).fetchall()
        if rows:
            conn.execute(f"TRUNCATE {', '.join(f'"{s}"."{t}"' for s, t in rows)} CASCADE")


@pytest.fixture(scope="session", autouse=True)
def migrate() -> Iterator[None]:
    _ensure_database()
    _empty_tables()
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


OBSERVED_ERRORS: set[tuple[str, str, str]] = set()


def _recording(original: Callable[..., Any]) -> Callable[..., Any]:
    async def handler(request: Request, exc: Exception) -> Any:
        response = await original(request, exc)
        error_code = json.loads(response.body)["error"]["code"]
        OBSERVED_ERRORS.add((request.method.lower(), request.url.path, error_code))
        return response

    return handler


@pytest.fixture(scope="session", autouse=True)
def errors_are_documented() -> Iterator[None]:
    """테스트가 실제로 받은 에러 코드는 **그 엔드포인트의** 스펙에 선언돼 있어야 한다.

    스펙(`app.core.openapi`)은 손으로 적은 `raises(...)` 와 의존성에서 도출한 것의 합이다.
    서비스에 새 에러를 던지고 선언을 빠뜨리면 여기서 걸린다. 세션 끝에 한 번 검사한다.
    """
    originals = {exc: app.exception_handlers[exc] for exc in (AppError, RequestValidationError)}
    for exc, original in originals.items():
        app.exception_handlers[exc] = _recording(original)
    yield
    app.exception_handlers.update(originals)
    paths = app.openapi()["paths"]

    def declared(method: str, path: str) -> set[str]:
        for template, ops in paths.items():
            if method in ops and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path):
                return {
                    c
                    for r in ops[method]["responses"].values()
                    for c in r.get("content", {}).get("application/json", {}).get("examples", {})
                }
        return set()  # 없는 라우트(405·404)는 AppError 를 내지 않는다

    undeclared = sorted(
        (method, template, error_code)
        for method, template, error_code in OBSERVED_ERRORS
        if error_code not in declared(method, template)
    )
    assert not undeclared, f"스펙에 없는 에러가 실제로 나갔다: {undeclared}"


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


@pytest.fixture(autouse=True)
def clean_uploads() -> Iterator[None]:
    FakeStorage.uploaded.clear()
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test/v1") as c:
        yield c


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


class FakeStorage:
    """스토리지 포트의 테스트 구현. presigned URL 발급에 S3 를 부르지 않는다.

    `uploaded` 는 "클라이언트가 PUT 을 끝낸 key" 집합이다. 의존성 주입이 요청마다
    새 인스턴스를 만들므로 클래스 변수로 둔다.
    """

    uploaded: ClassVar[dict[str, bytes]] = {}

    def presigned_put(self, key: str, content_type: str) -> PresignedUpload:
        return PresignedUpload(url=f"https://s3.test/{key}?signature=fake", expires_in=600)

    def exists(self, key: str) -> bool:
        return key in FakeStorage.uploaded

    def read(self, key: str) -> bytes:
        return FakeStorage.uploaded[key]


class StopConsuming(BaseException):
    """FakeQueue 가 줄 배치를 다 쓰면 던진다. BaseException 이라 소비 루프의
    `except Exception` 에 잡히지 않고 테스트까지 올라온다."""


class FakeQueue:
    """큐 포트의 테스트 구현. 테스트가 elasticmq 를 띄우지 않아도 되게 한다.

    `batches` 는 receive 가 차례로 돌려줄 것이다. 예외 인스턴스를 넣으면 그 차례에 던진다.
    """

    def __init__(self, batches: list[Any] | None = None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.batches = list(batches or [])
        self.deleted: list[str] = []

    def send(self, queue_name: str, body: dict) -> None:
        self.sent.append((queue_name, body))

    def receive(self, queue_name: str, max_messages: int, wait_seconds: int) -> list[Any]:
        if not self.batches:
            raise StopConsuming
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return list(batch)

    def delete(self, queue_name: str, receipt: str) -> None:
        self.deleted.append(receipt)


class FakeGoogle:
    """Google 포트의 테스트 구현.

    ``oauth_code`` 를 ``"{sub}|{email}"`` 로 읽는다. 실제 프로덕션 경로(2단계 가입)를
    그대로 통과시키기 위한 것이다 — 로컬 가입 같은 우회 엔드포인트를 만들지 않는다
    (CLAUDE.md §4.4).
    """

    async def exchange(self, oauth_code: str) -> GoogleIdentity:
        sub, _, email = oauth_code.partition("|")
        return GoogleIdentity(sub=sub, email=email or None)


class FakeAI:
    """AI 포트(`app.ai.client.AIClient`)의 테스트 구현.

    패키지의 `USE_FAKE_LLM=1` 은 빈 응답뿐이라 시나리오를 줄 수 없다. 여기서는 돌려줄 결과를
    순서대로 넣는다(`FakeAI(결과1, 결과2)`). 다 쓰면 빈 성공 응답이다.
    받은 호출은 `calls` 에 남는다.
    """

    EMPTY_NLCD: ClassVar[dict[str, list[Any]]] = {
        "personality_tags": [],
        "core_values": [],
        "influence_relations": [],
        "emotion_keywords": [],
    }

    def __init__(self, *results: dict[str, Any]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _next(self, name: str, *args: Any, default: Any = None) -> dict[str, Any]:
        self.calls.append((name, args))
        if self.results:
            return self.results.pop(0)
        return {"data": {} if default is None else default, "meta": {}}

    def run_nlcd(self, source_text: str) -> dict[str, Any]:
        return self._next("run_nlcd", source_text, default=dict(self.EMPTY_NLCD))

    def run_rex(self, manuscript_text: str) -> dict[str, Any]:
        return self._next("run_rex", manuscript_text)

    def run_aiq(
        self,
        question: str,
        manuscript_text: str,
        scope: str = "project",
        selection_range: Any = None,
        messages: Any = None,
    ) -> dict[str, Any]:
        return self._next("run_aiq", question, manuscript_text, scope, selection_range, messages)

    def run_scds_rules(
        self, event: Any, world_rules: Any, characters: Any = None
    ) -> dict[str, Any]:
        """룰 검출은 LLM 이 없는 순수 함수다 — 결과를 넣지 않았으면 **실제 패키지**로 돌린다."""
        if self.results:
            return self._next("run_scds_rules", event, world_rules, characters)
        from prolog_ai import run_scds_rules

        self.calls.append(("run_scds_rules", (event, world_rules, characters)))
        return run_scds_rules(event, world_rules, characters)

    def run_scds_analysis(
        self, event: Any, rule_result: Any, characters: Any = None
    ) -> dict[str, Any]:
        return self._next("run_scds_analysis", event, rule_result, characters)

    def run_ssm(self, manuscript_text: str, characters: Any = None) -> dict[str, Any]:
        return self._next("run_ssm", manuscript_text)


app.dependency_overrides[google_oauth] = FakeGoogle


def fake_ai() -> FakeAI:
    """FastAPI 는 클래스를 넘기면 `__init__(*results)` 를 요청 인자로 읽는다.

    인자 없는 공장으로 준다.
    """
    return FakeAI()


app.dependency_overrides[ai_client] = fake_ai
app.dependency_overrides[storage] = FakeStorage


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
