# CLAUDE.md — Prolog / StoryForge Backend

> **문서 이력.** 원본 CLAUDE.md는 2026-09-10 유실되었고 복구하지 못했다.
> 이 문서는 `.importlinter`(계약 정본)와 `ROADMAP.md`(Phase 정의·미결 항목)에서
> 살아남은 내용을 복원해 재작성한 것이다.
> **소실분:** 구 §5·§9·§11의 서술문, 코딩 컨벤션, "규칙 2"의 내용.
> 규칙 2는 `.importlinter`에 계약이 없어 기계로 강제되지 않던 조항으로 보인다.
> 유실 이후 확정된 사항: AUTH를 Google 단일 로그인으로 전환(v0.2).

---

## 1. 프로젝트

| 항목 | 값 |
| --- | --- |
| 서비스 | StoryForge — AI 기반 스토리 구조 관리 IDE |
| 레포 경로 | `~/dev/prolog` (2026-09-30 iCloud 동기화 밖으로 이동. 옛 경로 `~/Desktop/전공동`) |
| 스택 | FastAPI · PostgreSQL · SQLAlchemy(asyncpg) · Alembic(psycopg) · uv |
| 현재 단계 | **Phase 2b 진행 중** — `prolog-ai` 연동: AI 포트 · `ai` 워커 · **NLCD 완료**. 다음은 REX · AIQ · SCDS · SSM 의 AI 경로 |
| git | `a6ea4e9` 첫 커밋(84파일) → `d00610a` CLAUDE.md 복원 → `f586427` AUTH v0.2 → `66b4f67` 감사 P0 → `3f0eca7` 감사 P1 → `87c9006` 조합 레이어 → `a382a5c` 감사 P2 → `8d20b20` 감사 P3 → `a381109` OpenAPI → `583e00a` PG16 → `ec649f0` Phase 1 인프라 → `205d0dd` manuscript_count → `73427d3` NOTI → `c10290d` OpenAPI → `a2d4471` MSU·챕터 → `5a2d79f` 업로드 콜백·s3mock·복합 FK → `62d5837` 추출 워커 → `a19dd01` OpenAPI 계약·목록 커서·팀 유래 멤버 → `cf65615` 검증 스크립트 → `56c197f` 스키마 이름·seed → `a9e979b` Phase 2a 잡 내구성 → ASS 수동 경로(이 문서와 같은 커밋) |

### 실행

dev DB 는 **compose 의 PostgreSQL 16** 이다. 호스트 포트는 **5433** — 로컬 PostgreSQL 15 가
5432 를 쓰고 있어서다. `pgdata` 볼륨이 붙어 있으므로 `down` 해도 데이터는 남는다
(`down -v` 는 지운다).

```bash
cd ~/dev/prolog && docker compose up -d   # 프로젝트 이름은 compose 파일의 `name: prolog`
cd ~/dev/prolog && uv run uvicorn app.main:app --reload
```

### 테스트

```bash
cd ~/dev/prolog && DATABASE_URL="postgresql+asyncpg://prolog:prolog@localhost:5433/prolog_test" uv run pytest -q
```

### 품질 게이트 (커밋 전 필수)

```bash
cd ~/dev/prolog && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy app && uv run lint-imports \
  && DATABASE_URL="postgresql+asyncpg://prolog:prolog@localhost:5433/prolog_test" uv run pytest -q
```

---

## 2. 절대 규칙 — 어기면 되돌릴 수 없다

1. **`.env`를 커밋하지 않는다.** `.gitignore`에 등재되어 있고 첫 커밋에서 제외를 확인했다.
   JWT 서명 키와 Google client secret이 들어 있다.
2. **테스트는 `_test`로 끝나는 DB에서만 돈다.** `tests/conftest.py`의 `pytest_configure` 가드를 제거하지 않는다.
   **`os.environ.setdefault("DATABASE_URL", ...)`를 되살리지 않는다** — 암묵 기본값이 있으면 가드가 영원히 발화하지 않는다.
   가드는 DB 이름 부분만 잘라 검사한다(호스트에 `_test`가 들어가도 통과하지 않게).
3. **`TRUNCATE ... CASCADE`를 dev DB에 쓰지 않는다.** 38테이블 5스키마에서 참조 체인 전체가 비워진다.
4. **비밀번호를 저장하지 않는다.** Google OAuth 단일이다. bcrypt/argon2를 되살리지 않는다.
5. **디스크 여유를 확인하고 시작한다.** 2026-09 작업 중 ENOSPC로 전체가 멈춘 적이 있다.
   `df -h /System/Volumes/Data`가 5GB 미만이면 작업을 시작하지 않는다.
   **저장소를 iCloud 동기화 폴더(Desktop·Documents)에 두지 않는다.** 예전 위치 `~/Desktop/전공동` 에서
   디스크가 차자 macOS 가 `.venv`·소스·`.git` 파일을 클라우드로 내보냈고(dataless), import 가 수십 분
   멈추다 `Errno 60` 로 죽었다. 편집 도중 파일 읽기가 불완전하게 끝난 적도 있다. 그래서 `~/dev/prolog` 로
   옮겼다. 증상이 다시 보이면: `find . -flags +dataless | wc -l`.

---

## 3. 아키텍처 — Ring 구조

`.importlinter`가 계약의 **정본**이다. 이 문서의 서술과 어긋나면 `.importlinter`를 따른다.

| Ring | 패키지 | 모듈 |
| --- | --- | --- |
| — | `app.api` | 조합 레이어. 모든 Ring 위에 있다 (아래 규칙 참조) |
| 1 | `app.platform_` | `auth` · `teams` · `projects` · `notifications` |
| 2 | `app.content` | `manuscripts` — 원고·챕터·추출 (Phase 1 완료) |
| 3 | `app.authoring` | `nlcd`(자연어 추출 잡) · `ass`(캐릭터 초안·확정 — 수동 경로) · `rex`(세계관 규칙 CRUD — 수동 경로) |
| 4 | `app.insight` | `scds` · `ssm` · `aiq` · `rcv` · `fts`(복선 — 사용자 지정, AI 없음) |

**경로 주의:** AUTH 모듈은 `app/platform_/auth/`다. `app/modules/auth/`가 아니다.
언더스코어가 붙은 `platform_`인 이유는 파이썬 표준 라이브러리 `platform`과의 충돌 회피다.

### 계약 5종

| 계약 | 내용 |
| --- | --- |
| `rings` (규칙 1) | 의존은 `api → insight → authoring → content → platform_` 한 방향 |
| `ring1-independent` (규칙 3) | `auth`·`teams`·`projects`·`notifications` 끼리 동기 호출 금지 |
| `ring3-independent` (규칙 3) | `nlcd`·`ass`·`rex` 끼리 동기 호출 금지 |
| `ring4-independent` (규칙 3) | `scds`·`ssm`·`aiq`·`rcv`·`fts` 끼리 동기 호출 금지 |
| `core-is-a-leaf` (규칙 4) | `app.core`·`app.domain`은 어떤 모듈도 import 하지 않는다 |

**계약을 느슨하게 고쳐서 통과시키지 않는다.** 통과하지 못하는 코드는 커밋하지 않는다.

### 승인된 예외 — 범위로 정의한다

`app/core/deps.py`가 platform 멤버십 테이블(`team_members` · `project_members`)을
**권한 판정 목적으로** 원시 SQL로 읽는다. **이것이 예외의 범위다. 함수 개수는 기준이 아니다** —
권한 판정에 필요하면 함수를 더 만들어도 같은 예외 안이고, 목적이 다르면 함수가 하나여도 범위 밖이다.

범위 안: 역할 조회·권한 임계값 비교·소속 여부로 접근을 가르는 것
(`require_project_role` / `require_team_role` / `assert_team_role` / `team_ids_of`).

**범위 밖: 표시용 데이터(username 등)와 리소스 존재 확인.**
이건 `app/api/` 조합 레이어에서 각 모듈을 호출해 처리한다. `core`에 끌어들이지 않는다.

import가 아니라 SQL이므로 `core-is-a-leaf` 계약에 걸리지 않는다. **그래서 위험하다** —
린터가 지켜주지 못하는 지점이다. 범위를 넘겨야 할 상황이면 `platform`을 `app/domain/`의
공유 커널로 승격해 계약으로 표현 가능한 정상 관계로 만들자고 제안한다 (ROADMAP P2의 선택지 2).

### `app/api/` — 조합 레이어

**두 개 이상 모듈의 데이터가 한 응답에 필요할 때만 여기로 올린다.** 그 외 엔드포인트는
모듈 라우터에 남는다. 모듈끼리는 서로를 import 하지 못하지만(규칙 3) 이 레이어는 모든 Ring
위에 있어 각 모듈을 호출해 응답을 합칠 수 있다. 반대 방향(모듈 → `api`)은 `rings` 계약이 막는다.

**캐릭터 초안은 ASS 소유다** (`app/authoring/ass/models.py`). ERD 가 "ASS 의 핵심" 으로 두고 ASS 가
편집·확정한다. NLCD 는 추출 결과를 넘길 뿐이고, `ring3-independent` 때문에 import 가 아니라
이벤트로 넘긴다(2b). 한때 초안 모델이 `nlcd/models.py` 에 있었다 — 그대로 두면 확정이 규칙 3 을 어긴다.

현재 올라와 있는 것: 멤버 목록(`username` 합성) · 초대 생성(`invited_email` → 사용자 조회) ·
`GET /teams/{teamId}/projects`(TEAM 경로에 PRJ 데이터).

원시 SQL 문자열은 `test_schema.py`의 모델↔DB 대칭 검사 **바깥**이다.
컬럼을 리네임하면 mypy도 테스트도 잡지 못한다. 직접 확인한다.

### 워커 · 큐 (Phase 1에서 생김, 2a 에서 내구성)

앱은 FastAPI 하나지만 **워커는 별도 프로세스 5종**이다. 전부 `app/` 을 그대로 import 한다.
Celery 는 쓰지 않는다 — 단순 루프 + SQS redrive 로 충분하다(2b 에서 필요해지면 다시 판단).

| 워커 | 하는 일 | 소비 큐 |
| --- | --- | --- |
| `worker/outbox_relay.py` | `published_at IS NULL` 폴링 → 큐 발송 → 표시 | (생산자) |
| `worker/notifier.py` | 도메인 이벤트 소비: 알림 INSERT · `chapter.deleted` → FTS 정리 (소비자별 inbox) | `notify` |
| `worker/extractor.py` | `manuscript_extraction` 잡 → S3 읽기 → 본문 채우고 `ready` | `io` |
| `worker/sweeper.py` | 주기 작업: 좀비 잡 회수 · 업로드 콜백 유실 회수 | (없음) |
| `worker/ai_worker.py` | AI 잡 — `job_type` 별 핸들러(`HANDLERS`). 지금은 `nl_extraction` | `ai` |

**내구성 규칙 (2a).** 어기면 durability.py 가 다시 재현한다.
- **메시지 하나가 워커를 죽이지 않는다.** 소비 루프는 `app/events/consumer.py` 하나다. 처리에
  실패한 메시지는 지우지 않는다 → 재수신 → `queue_max_receive_count` 번 뒤 `{queue}-dlq`.
- **경합하는 잡 전이는 전부 조건부 UPDATE 다** (`app/jobs/service.py`). 선점은
  `WHERE status='queued'` 이고 `attempt` 를 1 올린다. 완료·실패는
  `WHERE status='running' AND attempt=:선점때값` — status 만 보면 재시도 후 다른 워커가 다시
  선점한 잡(status 가 또 running)에 늦게 돌아온 워커가 결과를 쓴다.
- **잡 선점은 먼저 커밋한다.** `running` 이 보여야 스위퍼가 좀비를 줍는다. 결과는 두 번째
  트랜잭션에서 잡 완료와 함께 대상에 쓴다.
- **자동 재시도는 인프라 실패만** (좀비 `JOB_TIMEOUT` · 스토리지 읽기). 파일 탓인 실패는 즉시 끝.
  시도를 다 쓰면 `failed` 로 남고 `ALARM` ERROR 로그. 대상 실패 처리는 `sweeper.ON_FAILED` 훅.
- 임계치는 전부 Settings 다: `job_zombie_seconds_io|ai` · `upload_sweep_after_seconds` ·
  `queue_max_receive_count` · `sweeper_interval_seconds`. 장편 추출이 5분을 넘기면 io 값을 올린다.

**Ring 을 거스르는 알림은 이벤트다.** 아래 Ring 이 위 Ring 의 데이터를 정리해야 하면(예: 챕터 삭제 →
복선 orphaned) 아래 Ring 은 outbox 이벤트만 남기고, 위 Ring 이 `worker/notifier.py` 를 통해 받는다
(`app/insight/fts/events.py`). 그래서 `foreshadowing_chapters.chapter_id` 에는 FK 가 없다(0006) —
RESTRICT 면 MSU 가 FTS 를 알아야 하고, CASCADE 면 이벤트가 닿기 전에 참조 정보가 사라진다.

**큐는 3종이다**: `io`(잡) · `ai`(Phase 3 잡) · `notify`(도메인 이벤트).
릴레이가 `aggregate_type == 'job'` 이면 payload 의 `queue` 로, 아니면 `notify` 로 보낸다.
**한 큐에 소비자를 둘 붙이지 않는다** — SQS 는 메시지를 하나에게만 주므로 서로의 메시지를
받아 지운다. 상세는 ROADMAP Phase 1.

릴레이는 **at-least-once** 다. 중복 수신을 막는 것은 수신자의 책임이다 — 부작용과 같은 트랜잭션에서
`app/events/inbox.first_time(consumer, event_id)` 로 선점한다(`ops.processed_events`, `0003`).
잡 소비자는 선점(조건부 UPDATE)이 같은 일을 한다. `jobs.idempotency_key` 는 잡 **생성** 중복용이고 2b 에서 쓴다.

### OpenAPI — 스펙이 곧 계약이다

프론트가 `openapi.json` 을 본다. 스펙이 실제와 다르면 그게 버그다.

- 성공 응답은 `response_model=Envelope[X]` / `Page[X]` (`app/core/response.py`).
- 도메인 에러는 **라우트가 `responses=raises(...)` 로 서비스가 던지는 것만** 적는다.
  인증·권한·입력 검증(401·403·없는 팀/프로젝트 404·400)은 `app/core/openapi.py` 가
  의존성 그래프에서 도출한다 — 새 의존성이 에러를 던지면 `deps.DEPENDENCY_RAISES` 에 등록한다.
- 422 는 스펙에서 지운다. 실제 검증 실패는 400 `INVALID_INPUT` 이다.
- **스키마 이름은 프론트 생성 코드의 타입 이름이다 — 한 번 정하면 바꾸지 않는다.** PascalCase 만
  쓴다. 봉투는 `XEnvelope` · `XPage` · `XListEnvelope` (`Envelope.model_parametrized_name`).
  pydantic 은 제네릭 키를 `Envelope_X_` 로 만들므로 빌더가 모델 title 로 키를 바꾸고
  `$ref` 를 다시 쓴다. 같은 이름이 둘이면 스펙 생성이 멈춘다.
- `tests/conftest.py` 의 `errors_are_documented` 가 **테스트가 실제로 받은 에러 코드**를
  그 엔드포인트 스펙과 대조한다. 선언을 빠뜨리면 세션이 실패한다.
- 스냅샷 갱신: `uv run python -c "import json; from app.main import app; json.dump(app.openapi(),
  open('openapi.json','w'), ensure_ascii=False, indent=2, sort_keys=True)"`
- 검사: `uv run python -m scripts.verify.contract [e2e 응답 JSON]` — 어기면 exit 1.

### 외부 의존은 전부 포트다

테스트가 외부를 부르지 않게 인터페이스로 끊고 fake 를 주입한다. 새 외부 의존이 생기면 같은 방식으로.

| 포트 | 실제 구현 | 테스트 |
| --- | --- | --- |
| `GoogleOAuth` | Google 토큰 교환·ID 토큰 검증 | `FakeGoogle` |
| `Queue` | boto3 → elasticmq/SQS | `FakeQueue` |
| `Storage` | boto3 → s3mock/S3 (presigned·head·get) | `FakeStorage` |
| `Extractor` | stdlib (`txt`·`docx`) | `FileExtractor` 직접 |
| `AIClient` | `prolog-ai` 패키지(`app/ai/client.py`) | `FakeAI`(결과를 순서대로 준다) |

**AI 는 `app/ai/client.py` 로만 부른다.** 패키지는 동기이고 예외를 던지지 않는다(`data` | `error`).
워커 핸들러는 포트를 인자로 받고(스레드에서 호출), 요청 경로에서 부를 곳(SCDS 룰 검출)은
`Depends(ai_client)` — 테스트는 `dependency_overrides`. 패키지의 `USE_FAKE_LLM=1` 은 빈 응답뿐이라
테스트 시나리오에 쓰지 않는다. **자동 재시도는 `AI_*_TIMEOUT` 만**(`is_retryable` — 근거는 주석).
패키지가 usage 를 주지 않아 비용 컬럼은 없다. ai 큐 visibility 15분 · io 60초(`queue_visibility_*`).

**지원 파일 형식의 단일 출처는 `extraction.SUPPORTED_FORMATS` 다.** 발급 허용 목록과 추출 가능
목록을 따로 두면 어긋난다 — 실제로 pdf 가 발급은 되는데 추출에서 반드시 실패한 적이 있다.

---

## 4. 명세 정본

명세는 **Notion**에 있다. 코드와 어긋나면 Notion이 정본이다.

| 문서 | 위치 |
| --- | --- |
| 폴더 | `prolog API 명세 정리` |
| AUTH | `로그인/회원가입 기능 API 명세 (AUTH)` — **v0.2, Google 단일** |
| TEAM | `팀 생성 기능 API 명세 (TEAM)` |
| PRJ | `프로젝트 생성 기능 API 명세 (PRJ)` |
| ERD | `🗂️ Prolog 최종 아키텍쳐 / 01. platform` (Ring 1) |
| 아키텍처 | `🏗️ Prolog 백엔드 아키텍처 설계서` |

**명세에 없는 것을 발명하지 않는다.** 불가피하면 `# ASSUMPTION:` 주석을 남기고 보고에 별도 목록으로 올린다.
**명세를 읽지 않은 채로 엔드포인트를 구현하지 않는다.**

명세가 틀렸다고 판단되면 코드를 먼저 고치지 말고 Notion 수정을 제안한다. 정본이 둘이 되게 하지 않는다.

> ⚠️ **AUTH 페이지 현재 상태.** Notion AUTH 문서는 v0.2 전환 중 **일부만 수정된 상태**다.
> 공통사항·에러코드·데이터모델은 v0.2로 갱신되었으나, §3 엔드포인트 목록과 §4 상세에는
> 아직 로컬 가입/로그인·네이버·비밀번호 재설정 서술이 남아 있다.
> **§3·§4는 무시하고 아래 5절을 계약으로 삼아라.** 문서 정리가 끝나면 이 경고를 삭제한다.

---

## 5. AUTH 계약 — v0.2 (Google 단일)

### 5.1 엔드포인트

| # | Method | Path | 설명 |
| --- | --- | --- | --- |
| 1 | POST | `/v1/auth/oauth/google` | Google 인증. 기존 사용자는 로그인, 신규는 `signup_ticket` 발급 |
| 2 | POST | `/v1/auth/signup` | 가입 완료. `signup_ticket` + `username` + `role` |
| 3 | POST | `/v1/auth/token/refresh` | 액세스 토큰 재발급 (로테이션) |
| 4 | POST | `/v1/auth/logout` | 리프레시 토큰 폐기 |
| 5 | GET | `/v1/users/me` | 내 정보 조회 |
| 6 | PATCH | `/v1/users/me` | 내 정보 수정 (`role`만) |
| 7 | GET | `/v1/users/check-username` | 아이디 중복 확인 (인증 불필요) |

경로 주의: `/auth/refresh`가 아니라 **`/auth/token/refresh`**. `/auth/me`가 아니라 **`/users/me`**.
사용자 리소스는 `/users` 아래, 인증 동작만 `/auth` 아래.

### 5.2 2단계 가입 흐름

Google authorization code는 **1회용**이다. 코드를 교환해 신규임을 알아낸 뒤 400을 던지고
같은 코드로 재요청하게 하면 Google이 거부한다. 따라서 두 호출로 분리한다.

```
POST /auth/oauth/google  { oauth_code }
  → 기존: 200 + user + tokens, meta.is_new_user = false
  → 신규: 200 + { signup_ticket, email }, meta.is_new_user = true   (계정 생성 안 함)

POST /auth/signup        { signup_ticket, username, role }
  → 201 + user + tokens
```

`signup_ticket` = `sub`·`email`·`provider`를 담은 **TTL 10분 JWT, 전용 `aud`**.
테이블도 Redis도 쓰지 않는다. 검증 시 `aud`를 반드시 확인해 액세스 토큰으로 오용되지 않게 한다.

### 5.3 필수 구현 세부

- **`provider_user_id`에는 Google `sub`를 넣는다.** 이메일이 아니다.
  이메일은 변경 가능하고 `sub`는 불변이다. 기존 사용자 판별은 `(auth_provider, provider_user_id)` 조회.
- ID 토큰은 **서명·`aud`·`iss`·`exp`를 모두 검증**한다.
- `username`(varchar 30, NOT NULL, UNIQUE)과 `role`(`writer`/`aspiring_writer`/`reader`)은
  Google이 주지 않는다. 가입 완료 화면에서 사용자가 선택한다.
- 리프레시 토큰 **로테이션 재사용은 거부**한다. 폐기 토큰 재제출 → `REFRESH_TOKEN_INVALID`.

### 5.4 Google 검증을 포트로 분리한다

ID 토큰 검증과 code 교환을 인터페이스로 추상화하고, 테스트에서 fake를 주입한다.

- 테스트가 Google을 실제로 호출하면 안 된다.
- **로컬 가입 엔드포인트를 테스트 편의용으로 되살리지 않는다.** 인증 경로가 둘이 되면
  설정 실수 하나로 비밀번호 계정 생성 경로가 열린다.
- fake 주입 방식이면 테스트가 실제 프로덕션 경로를 그대로 통과한다.
- dev 계정이 필요하면 seed 스크립트로 행을 직접 넣는다.

설정값: `GOOGLE_CLIENT_ID` · `GOOGLE_CLIENT_SECRET` (`.env.example`에 등재됨).
리다이렉트 URI가 필요하면 `GOOGLE_REDIRECT_URI`로 추가한다.

### 5.5 AUTH 에러 코드

| 코드 | HTTP |
| --- | --- |
| `INVALID_INPUT` | 400 |
| `USERNAME_REQUIRED` | 400 |
| `USERNAME_TAKEN` | 409 |
| `UNAUTHORIZED` | 401 |
| `SIGNUP_TICKET_INVALID` | 401 |
| `REFRESH_TOKEN_INVALID` | 401 |
| `OAUTH_PROVIDER_ERROR` | 502 |
| `USER_NOT_FOUND` | 404 |

**삭제됨:** `INVALID_CREDENTIALS`, `EMAIL_TAKEN`, `SOCIAL_ONLY_ACCOUNT`.
**존재하지 않음:** `VALIDATION_ERROR` — 명세는 `INVALID_INPUT`이다. AUTH에 `FORBIDDEN`은 없다.

---

## 6. TEAM · PRJ 계약 주의점

명세를 직접 읽고 구현하되, 이전 구현이 틀렸던 지점만 명시한다.

### 6.1 에러 코드는 모듈별로 이름이 다르다

| 상황 | TEAM | PRJ |
| --- | --- | --- |
| 멤버 없음 | `TEAM_MEMBER_NOT_FOUND` | `MEMBER_NOT_FOUND` |
| 초대 없음 | `TEAM_INVITATION_NOT_FOUND` | `INVITATION_NOT_FOUND` |
| 이미 멤버 | `ALREADY_TEAM_MEMBER` | `ALREADY_MEMBER` |

공통 처리로 뭉개지 않는다. 특이 상태 코드: **`INVITATION_EXPIRED`는 410**,
`LAST_OWNER_CANNOT_LEAVE`는 409(공통), `NOT_TEAM_MEMBER`는 403(PRJ 전용).

### 6.2 팀 프로젝트 생성 권한

`owner_type=team`일 때 요구되는 것은 **팀 소속 여부뿐**이다. 팀원 누구나 만들 수 있다.
`owner|admin`으로 제한하지 않는다. 비소속자는 `NOT_TEAM_MEMBER`(403) — 일반 `FORBIDDEN` 아님.

**확인 필요:** 팀 프로젝트 생성자가 `require_project_role`을 통과하는가.
팀 멤버를 `project_members`로 승격하지 않으면 생성자가 락아웃될 수 있다.

### 6.3 멤버 목록에 `username`을 포함한다

TEAM 4.6·PRJ 4.6 모두 응답에 "(사용자 이름 포함)"을 명시한다. `user_id`만 반환하면 명세 위반이며
프론트가 멤버 화면을 렌더할 수 없다. (ROADMAP P2 — 결정 필요 항목)

필요한 것은 `username` 하나다(이메일 아님). 선택지:

1. **상위 조합 레이어에서 합친다** — 라우터/조합 레이어는 모듈 위에 있으므로 규칙 3 위반이 아니다. 가장 싸다.
2. outbox 이벤트로 `username`만 비정규화.
3. `users`를 `app/domain/`의 공유 커널로 승격 — 되돌리기 가장 어렵다. 마지막 수단.

### 6.4 초대는 `token_hash` 링크로 처리한다

`invited_email == JWT.email` 매칭을 쓰지 않는다. 초대받은 주소와 Google 로그인 주소가
다른 경우가 흔하다(회사 메일로 초대, 개인 지메일로 로그인).

**구현됨.** `0001_initial.py`에 `token_hash varchar(64) NOT NULL UNIQUE`를 넣고
`responded_at`을 `accepted_at`으로 대체했다(거절 엔드포인트가 없으므로 "응답"의 유일한 의미가
수락이다. 취소 시각은 트리거가 유지하는 `updated_at`이 갖는다).

토큰은 **서명하지 않는다.** `secrets.token_urlsafe(48)`의 sha256만 저장하고 원문은 **초대 생성
응답에만** 담는다. 수락은 `POST .../invitations/{invitationId}/accept` 본문의 `{"token": "..."}`를
`compare_digest`로 대조한다. 틀린 토큰은 `TEAM_INVITATION_NOT_FOUND`/`INVITATION_NOT_FOUND`(404) —
명세에 없는 코드를 만들지 않는다. `INVITATION_EMAIL_MISMATCH`는 삭제됐다.

JWT를 쓰지 않는 이유: 만료·폐기가 이미 행(`status`·`expires_at`)에 있어 서명 토큰을 써도 결국
DB를 봐야 하고, 서명 키가 유출되면 초대를 위조할 수 있다. 해시 저장은 DB가 유출돼도 링크를
복원할 수 없다. `refresh_tokens`가 쓰는 패턴과 같다.

> ⚠️ 이전 판의 "ERD에 `token_hash` UK 컬럼이 이미 있다"는 **오기였다.** DDL에 없었고
> **정본은 DDL이다**(§7). ERD 쪽 표기는 아직 대조하지 않았다.

### 6.5 명세에 없는 것을 만들지 않는다

초대 **거절** 엔드포인트는 TEAM·PRJ 어디에도 없다. 초대자의 **취소**(`DELETE`)만 있다.

### 6.6 페이지네이션

**커서 기반**. `limit` 기본 20 / 최대 100. 오프셋은 명세 위반.

---

## 7. 스키마 규칙

- **raw SQL DDL이 정본이다.** `alembic/versions/0001_initial.py` 하나에 부분 인덱스·`lower()` UNIQUE·
  CHECK·트리거가 모두 들어 있다. autogenerate로는 만들 수 없다.
- **`0001` 직접 수정은 끝났다.** dev DB에 데이터가 있고 커밋이 쌓였다 — 고치면 이미 적용한
  사람의 DB와 파일이 조용히 어긋난다. 이제부터 **새 마이그레이션**이다 (`0002` 부터).
- **Alembic만 psycopg(동기)를 쓴다.** asyncpg는 한 execute에 여러 문장을 담지 못해 raw DDL이 깨진다.
  런타임은 asyncpg.
- `test_schema.py`가 매 실행마다 모델↔DB 컬럼 대칭과 트리거를 검사한다. 느슨하게 만들지 않는다.
- **소프트 삭제는 없다.** platform 스키마에 `deleted_at`이 없다.
  `projects.team_id`는 `ON DELETE RESTRICT`이고, 팀 삭제 409는 이 제약 위반을
  `TEAM_HAS_ACTIVE_PROJECTS`로 번역해 처리한다 (승인된 설계).
  **IntegrityError는 제약 이름 상수로 분기한다.** 메시지 문자열 파싱은 PG 마이너 버전에 깨진다.
- `teams.member_count`를 저장하지 않는다. 조회 시 집계한다 (동시 가입 경합).
- 38테이블(`0003` 의 `ops.processed_events` 포함) 중 Phase 0에서 **쓰는** 것은 platform 11개뿐이다. 나머지 26개도 제약을 박아 이미 만들었다.
  (v0.2에서 `password_resets`를 지워 38 → 37, platform 12 → 11이 되었다.)

### v0.2 스키마 변경

- `password_resets` 테이블 삭제
- `users.password_hash` 컬럼 삭제
- `users.provider_user_id` → NOT NULL
- `auth_provider` CHECK → `IN ('google')`
- `CHECK (auth_provider <> 'local' OR password_hash IS NOT NULL)` 삭제
- `UNIQUE (auth_provider, provider_user_id)` → 부분 조건 없이 UNIQUE
- `UNIQUE (lower(email)) WHERE email IS NOT NULL` 유지

**방침:** 새 마이그레이션을 추가하지 말고 `0001_initial.py`를 직접 고친 뒤
`git commit --amend`로 첫 커밋을 갱신한다. 커밋이 하나뿐이고 푸시된 적이 없으므로 비용이 없다.
새 마이그레이션으로 처리하면 "만들었다 바로 지우는" 화석이 히스토리에 남는다.

---

## 8. 로드맵 (ROADMAP.md 요약 — 상세는 그쪽이 정본)

| Phase | 내용 |
| --- | --- |
| 0 | AUTH · TEAM · PRJ. 완료 기준: 인증된 사용자가 프로젝트를 만들고 동료를 초대해 수락까지 마친다 |
| 1 | MSU · 챕터 · io 워커 · Outbox 릴레이 · NOTI |
| 2 | 잡 인프라 완성 (분수령) — 재시도 · 좀비 회수 · DLQ · 멱등키 |
| 3 | NLCD · ASS · SSM · AIQ — 모듈당 `job_handler.py` 1개 + 라우터 |
| 4 | SCDS · RCV · FTS |
| 5 | Outbox 확장 · SSE 준비 · 읽기 캐시 |

⚠️ **순서 충돌 (ROADMAP 77행):** 구 §13은 "텍스트 추출 잡"을 Phase 1에, "jobs 인프라"를 Phase 2에 뒀다.
권고는 Phase 1에서 jobs 최소 코어만 만들고 나머지를 Phase 2에서 얹는 것.

Phase 1 착수 전 필요: localstack 또는 elasticmq. Redis도 아직 아무도 쓰지 않는다.

---

## 9. 지금 할 일

**완료 (저장소):** `.gitignore`·`.env.example` 확인, `git init`, `.env` 제외 검증,
`pytest_configure` 가드, 게이트 5종, 첫 커밋 `a6ea4e9`.

**완료 (AUTH v0.2, `f586427`):**

1. `0001_initial.py`를 §7 목록대로 수정 (37테이블). **`--amend`는 하지 않았다** —
   그 사이 `d00610a`가 올라와 첫 커밋이 HEAD가 아니게 되었다. 0001을 직접 고쳤으므로
   마이그레이션 파일은 여전히 하나이고, 첫 커밋에 접어 넣으려면 rebase가 필요하다 (미결)
2. Google ID 토큰 검증 포트(`auth/google.py`) + fake 주입. `core/security`·의존성에서 bcrypt 제거
3. `app/platform_/auth/` 재작성 — `/auth/oauth/google` + `signup_ticket`, `/auth/signup`,
   `/users/check-username`
4. 경로 정정 (`/auth/token/refresh`, `/users/me`) + `PATCH /users/me`
5. 에러 코드 이름 정렬 (§5.5, §6.1). `VALIDATION_ERROR(422)` → `INVALID_INPUT(400)`
6. 테스트 픽스처를 fake Google 주입으로 전환. §10 필수 3종 추가 (28 → 33 테스트)

**완료 (TEAM·PRJ 감사):** 엔드포인트 24종을 Notion TEAM·PRJ 명세와 1:1 대조한 뒤
승인받은 순서로 처리했다.

- **P0** `66b4f67` — 팀 프로젝트 락아웃 해소(팀 멤버십을 프로젝트 권한으로 해석),
  생성 권한 완화 + `NOT_TEAM_MEMBER`
- **P1** `3f0eca7` — 식별자 필드명(`team_id`·`project_id`·`invitation_id`),
  `projects.name → title`, 초대 필드명, 수락 응답을 Member 로, 본인 탈퇴,
  IntegrityError 제약 이름 분기, `GET /projects` 팀 프로젝트 포함 + 쿼리 필터
- **P2** `a382a5c` — 조합 레이어 정착(멤버 목록 `username`, 초대 시점 `ALREADY_*`),
  계약 외 표면 제거(`reject` 2개·PRJ `GET /invitations`), `GET /teams/{teamId}/projects`,
  `member_count` 집계, 초대 role 에서 `owner` 제외
- **P3** — `INVALID_OWNER_TYPE → INVALID_INPUT`, 초대 `token_hash` 전환(§6.4),
  만료 초대 410 · 초대 취소 테스트

**완료 (Phase 1):** `ec649f0` elasticmq·Outbox 릴레이·잡 최소 코어 →
`205d0dd` `manuscript_count` →`73427d3` NOTI(인앱) → `a2d4471` MSU·챕터 →
`5a2d79f` 업로드 완료 콜백·s3mock·챕터 복합 FK → `62d5837` 추출 워커.
워커 3종을 실제 프로세스로 띄워 초대→알림, 업로드→추출 전 구간을 확인했다.

**완료 (검증 후속, `a19dd01`):** E2E 검증(`scripts/verify/`)에서 나온 4건 — OpenAPI 를
실제 계약으로(성공 스키마 39/39 · 에러 코드 28/29 · 422 0), 팀 멤버·프로젝트 멤버·챕터
목록 커서, 초대 수락 토큰 우선 검사, 팀 프로젝트 멤버 목록에 팀원 포함(`source`).
**완료 (프론트 착수 전):** 봉투 스키마 이름 정리(`Envelope_TeamResponse_` → `TeamResponseEnvelope`,
`contract.py` [5] 이름 규칙), dev DB 초기화 + `scripts/verify/seed.py`(사용자 1 · 팀 1 ·
프로젝트 2 · 원고 1 · 챕터 3).
**완료 (Phase 2a):** 워커 루프 내구성 · SQS DLQ · 이벤트 소비 멱등(`0003`) · 조건부 선점 ·
좀비 회수(attempt 대조) · 인프라 실패 자동 재시도 · 업로드 콜백 유실 스위퍼. 실물에서
`durability.py poison`·`dup` 둘 다 **통과**(재현되지 않음), 같은 시나리오가 `tests/test_durability.py` 에 있다.
**남겨 둔 것:** 받은 사람이 초대 토큰을 얻을 경로 없음(알림에 토큰 없음·메일 없음).
**완료 (ASS 수동 경로):** `origin='user_added'` 경로 15 오퍼레이션 — 빈 초안 · 초안 조회/목록/이름 수정 ·
항목 추가/수정/삭제 · 확정(create/merge) · 폐기 · 초안 이력(항목별 `item_id` 필터) · 캐릭터 목록/조회/
이름 수정/삭제 · 캐릭터 이력. `0004`(`source_job_id` 이름 · 빈 초안 `source_text` NULL ·
`characters.created_from_draft_id`/`confirmed_at` · 이력 `item_id`). e2e 에 캐릭터 경로, seed 에
확정 캐릭터 2 · 검토 대기 초안 1(화면 23 의 윤서). **AI 경로(NLCD forward · suggestions)는 없다.**
**완료 (REX 수동 경로):** 명세 §3 의 5~8(`/world-rules` 목록·추가·수정·삭제). `origin='user_added'` 만.
AI 추출(`rule-extractions` 1~4)은 2b. seed 에 규칙 2(화면 21).
**완료 (FTS):** 명세 15개 중 14 — 복선 CRUD · 연결 챕터 · 회수/취소 · 미회수 · 안내(템플릿) · 타임라인 ·
캐릭터 연결 · 챕터 역참조. `0006`(어휘 `unresolved|resolved|orphaned` · `setup|payoff|linked` ·
챕터 FK 제거 · 번호 캐시 제거). 챕터/원고 삭제 → `chapter.deleted` → orphaned(설치)·unresolved(회수).
**사건 연결은 없다**(SCDS 이후). seed 에 복선 2.
**완료 (NOTI 알림 설정):** 명세 §3 의 6·7. `0007` 로 `notification_settings` 를 사용자 × 유형 행으로
(화면 31 · 명세 §2.2 가 같다 — 한 행 + `muted_types` 로는 유형별 이메일을 표현 못 했다). 바꾼 유형만 행이
있고 없으면 둘 다 켜짐. `in_app_enabled = false` 인 유형은 알림을 만들지 않는다. 이메일은 저장만.
**완료 (원고 편집 이력):** MSU 명세 §4.10 `GET .../versions`. 정책은 `versions.py` 머리말 — 업로드 추출은
항상 새 스냅샷, 편집기 저장은 마지막 편집기 스냅샷이 5분 미만·길이 변화 1,000자 미만이면 **덮어쓰고**
아니면 새 스냅샷(ERD 기준 + 창의 마지막 상태 보존). `0008`(`source` · `(manuscript_id, version_no)` UNIQUE).
**완료 (NLCD, Phase 2b 첫 모듈):** 명세 5종 — 제출(조합 레이어) · 폴링 · 재시도 · 이력 · forward(조합 레이어).
추출은 `ops.jobs`(`nl_extraction`, `ai` 큐) 한 행이고 `extraction_id` 가 잡 id 다. forward 는 ASS 초안을
만든다(`source_job_id`, 항목 전부 `ai_extracted` + 근거). 잡 행을 잠가 두 번 눌러도 초안은 하나다.
**다음 (Phase 2b):** REX · `ai` 워커 · LLM 게이트웨이 · 핸들러 공통 계약 · `/retry` API ·
`jobs.idempotency_key`. LLM 공급자 · 비용 컬럼(`model`·`token_in/out`, DDL 에 없음) 결정 필요.

**다음:**

1. **명세 수정 제안을 Notion 에 반영** (코드가 아니라 문서 작업이다)

   표시: **[코드]** 코드가 맞음 → Notion 을 코드에 맞게 고친다 · **[명세]** 명세가 맞음 → 코드를
   고쳐야 한다(코드 작업이 남았다) · **[둘 다]** 명세에 먼저 추가하고 코드가 따른다.

   **AUTH**
   - [코드] §3·§4 에 남은 로컬 가입/로그인·네이버·비밀번호 재설정 서술 삭제 — v0.2 계약은 이 문서 §5
   - [둘 다] 이메일 UNIQUE 충돌(`users_email_lower_uq`) 코드 없음 — `EMAIL_CONFLICT`(409) 추가 후 매핑.
     지금은 500 이다(§12)

   **TEAM · PRJ**
   - [코드] 초대 생성 응답의 `token` 원문 · 수락 요청 본문 `{"token"}` (§6.4 `token_hash` 방식)
   - [코드] ERD 의 `token_hash` 표기 — ERD 에 없다(DDL 이 정본)
   - [코드] PRJ 초대 status 에 `revoked`(취소) · PRJ 초대 `expires_at` 이 명세에 없다
   - [코드] `DUPLICATE_INVITATION`(409) · `INVITATION_NOT_PENDING`(409) 이 명세 에러 코드 표에 없다
   - [코드] TEAM §4.11 권한 미지정 — 코드가 정한 권한을 적는다
   - [코드] PRJ §4.6 멤버 목록: 팀 프로젝트면 팀원 포함 + `source`(`project`|`team`), 팀 유래 멤버는
     `role = editor` · `joined_at` = 팀 가입 시각. §4.9·§4.11(역할 변경·내보내기)은 팀 유래 멤버에
     `MEMBER_NOT_FOUND` — "팀에서 처리한다" 서술 필요 (`a19dd01`)
   - [코드] TEAM·PRJ 멤버 목록 · 챕터 목록이 커서라는 것을 명세에 적는다
   - [명세] TEAM 초대 목록(`GET /teams/{teamId}/invitations`)이 아직 전체 반환 — §6.6 커서로 바꿔야 한다

   **MSU**
   - [코드] `source_type` `file`→`upload` · `file_url`→`file_key` · status `draft|processing|ready|failed` ·
     `Manuscript.error` 삭제(사유는 잡의 `error`) · 업로드 방식 multipart → presigned URL + `.../file/complete`
   - [코드] 챕터 경로 `/manuscripts/{m}/chapters` → `/projects/{p}/chapters`
   - [코드] 지원 형식 목록(`txt`·`docx`)을 명세에 적는다. 그 밖은 발급 단계에서 `UNSUPPORTED_FILE_FORMAT`
     (`details.supported`)
   - [코드] `FILE_TOO_LARGE`(413) 도달 불가 — presigned PUT 은 크기를 막지 못한다. 삭제하거나, 필요하면
     presigned POST(content-length-range)로 바꿀 때 되살린다
   - [코드] "업로드된 파일 없음"(complete 콜백인데 객체가 없음) 전용 코드가 없다 — 지금은 `INVALID_INPUT`
   - [코드] 편집 이력 §4.10: 목록에 본문 포함 · `version_id`·`version_no`·`source`·`char_count`·`updated_at`
     필드 · 디바운스 정책(5분 창 · 길이 변화 1,000자 · 창 안 덮어쓰기 · 업로드는 항상 새 스냅샷)을 적는다.
     되돌리기(restore)는 확장 항목 그대로
   - [코드] content ERD 의 `manuscript_versions` 에 `project_id`·`chapter_id`·`version_no`·`char_count`·
     `source` 가 없다

   **NOTI**
   - [코드] 설정 PATCH 는 보낸 채널만 바꾼다 · 설정 조회는 다섯 유형 고정 배열(페이지 없음)
   - [코드] 이메일 연동(§2.3 · §3 8~10 · `EMAIL_INTEGRATION_NOT_FOUND`·`EMAIL_ALREADY_CONNECTED`) —
     **구현하지 않기로 확정**(§12). 명세에서 빼거나 "보류" 로 표시. 화면 31 의 연동 영역도 같다

   **NLCD**
   - [코드] 실패 응답은 200 에 `data`(status=failed) + `error` — 성공이면 `error: null` 이 함께 온다
   - [코드] `analyzing` 은 잡 `queued`·`running` 의 별칭 · 제출·재시도 응답이 추출 전체(명세는 두세 필드)
   - [코드] 재시도는 `failed` 에서만, 그 밖은 `EXTRACTION_NOT_READY`(명세는 completed 만 적었다).
     자동 재시도를 다 쓴 추출도 사용자는 되살린다
   - [코드] forward 는 `target_character_id` 가 있으면 초안 이름을 그 캐릭터 이름으로 채운다 — 확정하면
     `DUPLICATE_CHARACTER_CANDIDATE` 로 병합이 이어진다(명세 "병합 대상 정보를 함께 실어"의 구현).
     동명 캐릭터가 여럿이면 후보는 가장 먼저 만든 쪽이라 대상과 다를 수 있다
   - [코드] 제출에 `name`(화면 22 "인물 이름") — 명세에 없다. forward 때 초안 이름이 되고, 비었으면
     `target_character_id` 의 이름을 쓴다. 추출이 이름을 뽑을 필요가 없어졌다(패키지 TODO 해소)
   - [코드] 영향 관계의 `type` 은 추출 결과에는 있고 초안 항목에서는 버린다(ASS 항목은 `value` 하나)
   - [코드] 중복 감지는 공백·대소문자만 무시한 같은 문장이다(명세는 "동일/유사")
   - [코드] 에러 코드는 패키지와 명세 §1.4 가 같다(`INVALID_INPUT` · `AI_EXTRACTION_FAILED` ·
     `AI_EXTRACTION_TIMEOUT`)

   **ASS**
   - [코드] 확정 캐릭터 속성을 `string[]` 이 아니라 `{attribute_id, field, value, evidence, origin}` 로
     (ERD: 확정 후에도 origin 이 보여야 한다)
   - [코드] 영향 관계를 `target/type/status` 가 아니라 `value` 하나로(DDL · 화면 23)
   - [코드] 초안 항목에 `evidence`(화면 "원문 근거") · 항목 PATCH 에 `field`(화면 "카테고리")
   - [코드] `source_session_id` → `source_job_id`(ERD)
   - [코드] 캐릭터 `PATCH`(이름)·`DELETE` 가 ASS 명세에 없다 — 추가하고 SCDS `PUT .../characters/{id}` 와
     역할을 나눈다
   - [코드] 편집 이력 목록 커서 · 항목에 `history_id`·`before/after_value`·`item_id`·`edited_by` ·
     초안 이력의 `item_id` 필터(화면 "편집 이력 N건" 은 항목 하나의 이력)
   - [코드] 폐기 응답이 `{draft_id, status}` 가 아니라 초안 전체
   - [코드] `create_new` 는 동명 캐릭터를 만든다(화면 37). ERD 의 `UNIQUE (project_id, lower(name))` 를
     "앱 레벨 409" 로 고친다(0005)
   - [코드] 초안 status: API `pending_review`(명세) · DB `pending` · ERD `editing` — ERD 를 맞춘다.
     ERD 에 `character_drafts.source_text` 추가 · `actor_user_id` → DDL `edited_by`
   - [코드] SCDS 명세의 `traits`/`values`/`influences` → ASS 이름(§12 결정, SCDS 명세 수정)

   **REX**
   - [코드] `title` 필수(화면 21 이 모든 규칙을 "R01 · 제목" + 설명으로 보여준다) · 응답에 `title`·
     `source_chapter_no`·`created_at` · `category` 는 API 에 내지 않는다(화면 미사용)
   - [코드] `extraction_id` 는 DDL `extraction_job_id` · 목록 커서 · 명세에 단건 조회가 없다(그대로)
   - [코드] ERD 의 `world_rules` 에 `title`·`category` 가 없다

   **FTS**
   - [코드] 챕터를 번호가 아니라 **id 로 받는다**(원고가 여럿이면 번호가 겹친다): `setup_chapter_id` ·
     `payoff_chapter_id` · `chapter_id`, 경로 `/chapters/{chapterId}/foreshadowings` ·
     `/linked-chapters/{chapterId}`. 응답은 번호 + `chapters[{chapter_id, chapter_no, role}]`
   - [코드] status 에 `orphaned`(설치 챕터 삭제, 명세 12항) · 연결 챕터 추가·회수 지정/취소 응답이 복선 전체
   - [코드] 목록 `meta.status_counts`(`unresolved`·`resolved`·`orphaned`, 필터 무관) — 화면 04 "미회수 3건 ·
     회수 완료 5건"
   - [코드] 안내 문구는 화면 24 · 미회수 목록은 현재 챕터 이전에 설치된 것만 · 타임라인·미회수·안내는 페이지 없음
   - [코드] 미회수 `sort` 두 값(`elapsed_desc`·`setup_chapter_asc`)이 같은 순서다 — 하나로 합친다
   - [코드] 사건 연결(`target_type=event`)은 SCDS 이후 · 링크 해제 경로 `/links/character/{targetId}`
   - [코드] insight ERD: `foreshadowing_chapters.chapter_id` 의 `ON DELETE RESTRICT` 는 `orphaned` 와 모순
     (설치 챕터가 지워지지 않는다) → FK 없음 + `chapter.deleted` 이벤트(0006). `setup_chapter_no`·
     `payoff_chapter_no` 캐시는 챕터 번호 변경 때 낡아서 없앴다

2. ~~NOTI 알림 설정~~ 해소(`0007`). 이메일 연동은 구현하지 않기로 확정(§12)
3. ~~`manuscript_versions` 디바운스~~ 결정·구현(`0008`, `app/content/manuscripts/versions.py`)

**완료 (PG16 전환):** compose 를 5433 으로 고정하고 `pgdata` 볼륨을 붙였다. 빈 볼륨에
마이그레이션을 새로 적용해 **부분 인덱스 11 · lower() 식 인덱스 7 · CHECK 36 ·
`updated_at` 트리거 37** 이 전부 올라오는 것을 카탈로그로 확인했고, 게이트 5종을 PG16 에서
통과시켰다. 로컬 PG15 는 5432 에 그대로 있다(끄지 않았다).

---

## 10. 반드시 추가할 테스트

현재 59 경로 · 90 오퍼레이션에 테스트 123개다 (`/v1/health` 제외).
**TEAM·PRJ 24개 오퍼레이션에 빠짐없이 테스트가 닿는다.**

이 절의 목록은 비었다 — 5종 모두 들어갔다. 리프레시 토큰 재사용 거부 · 테넌트 격리 ·
`signup_ticket` 오용 거부는 `f586427`, 유일 owner 409 는 그 이전, 만료 초대 410 은 P3 에서.

**응답 필드 집합**은 이제 `response_model` 이 강제한다 — 선언에 없는 필드는 직렬화에서 빠진다.
실제 응답과 스펙의 대조는 `scripts.verify.contract` 가 E2E 응답으로 한다(테스트 밖, 실물 인프라).

워커 루프는 `tests/test_durability.py` 가 FakeQueue 로 돈다(poison · 수신 실패 · dup · 좀비 경합 ·
시도 소진 · 콜백 유실). SQS redrive 자체(3회 뒤 DLQ 이동)는 elasticmq 의 동작이라 테스트 밖이다 —
`durability.py poison` 이 실물로 본다.

**다음에 얇은 곳:** 추출 핸들러가 **선점 후 · 완료 전**에 스위퍼에게 지는 경로는 서비스 단위
(`finish` 의 attempt 대조)로만 테스트한다. 핸들러 한가운데에 끼어드는 테스트는 없다.

---

## 11. 보고 형식

1. **게이트 결과** — ruff / format / mypy / lint-imports / pytest
2. **만든 것** — 범위별
3. **명세 편차** — 명세와 다르게 구현한 것. 승인 요청 형태로
4. **ASSUMPTION** — 명세에 없어서 발명한 것. 근거와 함께
5. **일부러 안 만든 것** — 이유와 함께
6. **확인 필요** — 검증되지 않은 것

"완료"를 선언할 때 **무엇에 대한 완료인지** 명시한다.
명세와 대조되지 않은 구현은 완료가 아니다.

---

## 12. 미결 항목

- **P3. 초대 메일 발송 없음** — Phase 1(NOTI)에서 해결. "내게 온 초대 목록" API도 없다.
  수락 방식은 `token_hash`로 전환 결정됨 (§6.4)
- 권한 Redis 캐시. ERD가 `(project_id, user_id) → role`을 60초 TTL 캐싱, 멤버 변경 시 무효화로 규정.
  필요한 인덱스 `project_members(user_id)`는 이미 있다
- outbox 릴레이 워커 (Phase 1) · Celery (Phase 2)
- 비멤버 접근 시 403 vs 404. 명세·구현 모두 403. 바꾸려면 명세부터
- **이메일 UNIQUE 충돌이 500이 된다.** `users_email_lower_uq` 위반에 매핑할 코드가 없다 —
  `EMAIL_TAKEN`이 §5.5에서 삭제됐기 때문이다. 서로 다른 Google `sub`가 같은 이메일을 갖는
  경우라 실무상 드물지만 구멍은 맞다. Notion에 `EMAIL_CONFLICT`(409) 추가를 검토하되,
  **명세 수정이므로 TEAM·PRJ 감사 때 다른 변경 건과 묶는다** (정본을 여러 번 건드리지 않는다)
- **해소됨 — `manuscript_count`** (`205d0dd`). Project 응답을 반환하는 엔드포인트를 전부
  조합 레이어로 올리고 `content.manuscripts` 를 `GROUP BY` 로 세어 붙인다
- **해소됨 — 업로드 콜백 유실** (Phase 2a). `worker/sweeper.py` 가 `upload_sweep_after_seconds`
  (기본 15분)가 지난 잡 없는 upload draft 를 훑어, S3 에 객체가 있으면 콜백과 같은 경로로 추출을
  건다. 객체가 없으면 건드리지 않는다(아직 안 올린 정상 draft). 원고 행을 잠가 콜백과 겹쳐도
  잡은 하나다. "발급 시각" 컬럼이 없어 `updated_at` 을 쓴다 — 제목을 고치면 그만큼 늦게 줍는다
- **결정 — 캐릭터 동명 허용** (`0005`). 화면 37 "새 인물로 만들기" 가 "같은 이름의 인물이 하나 더
  생겨요" 라고 알린 뒤 진행하므로 `characters_project_name_active_uq` 를 없앴다(ERD 의 "앱 레벨 경고로
  완화"). 중복 판정은 `ass.service._check_name` — resolution 없는 확정과 이름 변경만 409, `create_new` 는
  검사하지 않는다. 동시 확정은 이름 단위 `pg_advisory_xact_lock` 으로 줄 세운다(없으면 경합 테스트가 깨진다)
- **결정 — SCDS 이름 매핑은 별칭 없이 ASS 이름으로 통일한다** (`personality_tags`·`core_values`·
  `influence_relations`·`emotion_keywords`). ERD 가 이미 그렇게 결론냈고, 프론트가 아직 붙지 않아
  부채가 없다. SCDS 명세의 `traits`/`values`/`influences` 는 Notion 수정 대상이다
- **결정 — 이메일 연동(NOTI §2.3 · §3 8~10, 화면 31 "이메일 연동") 은 구현하지 않는다.** AUTH v0.2 가
  네이버를 제거했고 메일 발송 경로가 없다. `email_enabled` 설정은 저장만 하고 아무 것도 보내지 않는다.
  `platform.email_integrations` 테이블은 0001 에 남아 있지만 쓰지 않는다
- 프론트엔드 계약 미대조. `# ASSUMPTION:` 주석으로 표시되어 있으나,
  어긋나면 Phase 0 전체를 손봐야 한다 (ROADMAP 113행)
