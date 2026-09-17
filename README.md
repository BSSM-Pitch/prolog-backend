# Prolog (StoryForge) — 백엔드

계약과 설계 결정은 [CLAUDE.md](CLAUDE.md) 에 있다. 이 파일은 실행 방법만 적는다.

## 로컬 실행

```bash
docker compose up -d          # postgres 16 · redis
uv sync                       # python 3.12
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

`http://localhost:8000/docs` · 헬스체크 `GET /v1/health`

## 품질 게이트 (커밋 전 전부 통과)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run lint-imports
uv run pytest
```

테스트는 실제 Postgres 를 쓴다(SQLite 대체 금지 — 부분 인덱스·jsonb·text[]·DISTINCT ON 이 죽는다).
`DATABASE_URL` 이 가리키는 서버에 `prolog_test` DB 를 만들고 `alembic upgrade head` 를 돌린 뒤,
테스트마다 5개 스키마를 TRUNCATE 한다.

## 현재 상태

Phase 0 완료 — `core/`, AUTH, TEAM, PRJ, 권한 의존성, 38테이블 스키마.
다음은 Phase 1 (MSU · 챕터 CRUD · io 워커 · Outbox 릴레이 + NOTI).
