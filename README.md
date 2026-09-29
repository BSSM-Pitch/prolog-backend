# Prolog (StoryForge) — 백엔드

AI 기반 스토리 구조 관리 IDE 의 백엔드. FastAPI · PostgreSQL 16 · SQLAlchemy(asyncpg) · uv.

계약과 설계 결정은 [CLAUDE.md](CLAUDE.md) 에, 단계별 계획은 [ROADMAP.md](ROADMAP.md) 에 있다.
이 파일은 **실행 방법**만 적는다.

## 필요한 것

- Docker (compose v2)
- [uv](https://docs.astral.sh/uv/) · Python 3.12

## 시작하기

```bash
git clone https://github.com/BSSM-Pitch/prolog-backend.git
cd prolog-backend

docker compose up -d          # postgres · redis · elasticmq(SQS) · s3mock(S3)
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

`http://localhost:8000/docs` · 헬스체크 `GET /v1/health` · 스펙 스냅샷 [openapi.json](openapi.json)

> **포트가 이미 쓰이고 있다면** `.env` 에 `POSTGRES_PORT=5433` 처럼 지정하고
> `DATABASE_URL` 의 포트도 같이 바꾼다. `REDIS_PORT` · `SQS_PORT` · `S3_PORT` 도 같은 방식이다.
> 컨테이너 안쪽 포트는 그대로이므로 앱 설정만 맞추면 된다.

## 워커

앱과 별개 프로세스다. 필요한 것만 띄우면 된다.

```bash
uv run python -m worker.outbox_relay   # outbox → 큐 (io · ai · notify 로 라우팅)
uv run python -m worker.notifier       # 도메인 이벤트 → 알림
uv run python -m worker.extractor      # 업로드된 원고에서 텍스트 추출
uv run python -m worker.sweeper        # 주기 작업: 좀비 잡 회수 · 업로드 콜백 유실 회수
```

## 테스트

```bash
DATABASE_URL="postgresql+asyncpg://prolog:prolog@localhost:5432/prolog_test" uv run pytest -q
```

테스트는 **실제 Postgres** 를 쓴다(SQLite 대체 금지 — 부분 인덱스 · jsonb · `text[]` ·
`DISTINCT ON` 이 죽는다). `_test` 로 끝나는 DB 에서만 돌도록 `conftest.py` 가 막고 있으며,
그 DB 를 만들고 마이그레이션을 적용한 뒤 테스트마다 5개 스키마를 TRUNCATE 한다.
외부 의존(Google · SQS · S3)은 전부 포트로 끊고 fake 를 주입하므로 **네트워크를 타지 않는다.**

## 화면 개발용 데이터

```bash
uv run python -m scripts.verify.seed   # 사용자 dev · 팀 1 · 프로젝트 2 · 원고 1 · 챕터 3 (여러 번 돌려도 한 번만 들어간다)
```

처음부터 다시 넣으려면 `uv run alembic downgrade base && uv run alembic upgrade head` 후 seed.
seed 사용자는 실제 Google 로는 로그인할 수 없다. `scripts.verify.serve`(8001, Google 만 fake)에
`POST /v1/auth/oauth/google {"oauth_code": "seed-dev|dev@example.com"}` 로 토큰을 받는다.

## 실물 검증 (`scripts/verify/`)

테스트는 외부를 fake 로 끊는다. 실제 PG · elasticmq · s3mock 과 워커 프로세스로 전 구간을
돌려 보려면 이쪽을 쓴다. Google 만 fake 다. dev DB 에 새 사용자를 만든다.

```bash
uv run python -m scripts.verify.serve          # 8001 — Google 만 fake 인 로컬 전용 서버
uv run python -m worker.outbox_relay & uv run python -m worker.notifier & uv run python -m worker.extractor &
uv run python -m scripts.verify.e2e out.json   # 가입 → 팀·프로젝트 → 초대·수락 → 업로드·추출 → 알림
uv run python -m scripts.verify.contract out.json   # 스펙 검사 + 실제 응답 ↔ 스펙 대조 (어기면 exit 1)
```

`scripts.verify.durability` 는 워커 내구성을 실물로 판정한다(poison 메시지 → DLQ · 중복 이벤트 → 알림 1건).
재현되면 exit 1 이다. 사용법은 파일 머리말에 있다.

## 품질 게이트 (커밋 전 전부 통과)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run lint-imports
DATABASE_URL="postgresql+asyncpg://prolog:prolog@localhost:5432/prolog_test" uv run pytest -q
```

`lint-imports` 는 Ring 의존 방향과 모듈 간 직접 호출 금지를 기계로 강제한다 (`.importlinter`).
**계약을 느슨하게 고쳐서 통과시키지 않는다.**

## 구조

```
app/
  api/          조합 레이어 — 두 모듈 이상의 데이터가 한 응답에 필요할 때만
  platform_/    Ring 1 — auth · teams · projects · notifications
  content/      Ring 2 — manuscripts (원고 · 챕터 · 추출)
  authoring/    Ring 3 — nlcd · ass · rex
  insight/      Ring 4 — scds · ssm · aiq · rcv · fts
  core/         설정 · 에러 · 인증 의존성 (어떤 모듈도 import 하지 않는다)
  db/           Base · 세션 · alembic (raw SQL DDL 이 스키마의 정본)
worker/         outbox_relay · notifier · extractor
tests/
```

## 현재 상태

**Phase 1 완료.** AUTH(Google 단일) · TEAM · PRJ · MSU(원고 · 챕터) · NOTI(인앱) ·
Outbox 릴레이 · 잡 최소 코어까지 동작한다. 다음은 Phase 2 (잡 인프라 — 재시도 · 좀비 회수 ·
DLQ · 멱등키).
