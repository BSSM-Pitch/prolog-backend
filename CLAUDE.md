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
| 레포 경로 | `~/Desktop/전공동` |
| 스택 | FastAPI · PostgreSQL · SQLAlchemy(asyncpg) · Alembic(psycopg) · uv |
| 현재 단계 | Phase 0 — TEAM·PRJ 정렬 중 |
| git | `a6ea4e9` 첫 커밋(84파일) → `d00610a` CLAUDE.md 복원 → `f586427` AUTH v0.2 → `66b4f67` 감사 P0 → `3f0eca7` 감사 P1 → `87c9006` 조합 레이어 |

### 실행

```bash
cd ~/Desktop/전공동 && uv run uvicorn app.main:app --reload
```

### 테스트

```bash
cd ~/Desktop/전공동 && DATABASE_URL="postgresql+asyncpg://kmsmss@localhost:5432/prolog_test" uv run pytest -q
```

### 품질 게이트 (커밋 전 필수)

```bash
cd ~/Desktop/전공동 && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy app && uv run lint-imports \
  && DATABASE_URL="postgresql+asyncpg://kmsmss@localhost:5432/prolog_test" uv run pytest -q
```

---

## 2. 절대 규칙 — 어기면 되돌릴 수 없다

1. **`.env`를 커밋하지 않는다.** `.gitignore`에 등재되어 있고 첫 커밋에서 제외를 확인했다.
   JWT 서명 키와 Google client secret이 들어 있다.
2. **테스트는 `_test`로 끝나는 DB에서만 돈다.** `tests/conftest.py`의 `pytest_configure` 가드를 제거하지 않는다.
   **`os.environ.setdefault("DATABASE_URL", ...)`를 되살리지 않는다** — 암묵 기본값이 있으면 가드가 영원히 발화하지 않는다.
   가드는 DB 이름 부분만 잘라 검사한다(호스트에 `_test`가 들어가도 통과하지 않게).
3. **`TRUNCATE ... CASCADE`를 dev DB에 쓰지 않는다.** 37테이블 5스키마에서 참조 체인 전체가 비워진다.
4. **비밀번호를 저장하지 않는다.** Google OAuth 단일이다. bcrypt/argon2를 되살리지 않는다.
5. **디스크 여유를 확인하고 시작한다.** 2026-09 작업 중 ENOSPC로 전체가 멈춘 적이 있다.
   `df -h /System/Volumes/Data`가 5GB 미만이면 작업을 시작하지 않는다.

---

## 3. 아키텍처 — Ring 구조

`.importlinter`가 계약의 **정본**이다. 이 문서의 서술과 어긋나면 `.importlinter`를 따른다.

| Ring | 패키지 | 모듈 |
| --- | --- | --- |
| — | `app.api` | 조합 레이어. 모든 Ring 위에 있다 (아래 규칙 참조) |
| 1 | `app.platform_` | `auth` · `teams` · `projects` · `notifications` |
| 2 | `app.content` | (Phase 1에서 MSU·챕터) |
| 3 | `app.authoring` | `nlcd` · `ass` · `rex` |
| 4 | `app.insight` | `scds` · `ssm` · `aiq` · `rcv` · `fts` |

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

현재 올라와 있는 것: 멤버 목록(`username` 합성) · 초대 생성(`invited_email` → 사용자 조회) ·
`GET /teams/{teamId}/projects`(TEAM 경로에 PRJ 데이터).

원시 SQL 문자열은 `test_schema.py`의 모델↔DB 대칭 검사 **바깥**이다.
컬럼을 리네임하면 mypy도 테스트도 잡지 못한다. 직접 확인한다.

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

> ⚠️ **정정.** 이전 판의 "ERD에 `token_hash` UK 컬럼이 이미 있다"는 **오기다.**
> `0001_initial.py`의 `team_invitations`·`project_invitations`에는 `token_hash`도 `accepted_at`도
> **없다.** ERD와 DDL이 어긋나 있고 **정본은 DDL이다**(§7). 전환하려면 컬럼 추가가 선행되어야 하고,
> 첫 커밋 이후이므로 새 마이그레이션이 필요하다.

누락된 컬럼: `token_hash`, `accepted_at`. (`invited_by` FK는 있다.)

### 6.5 명세에 없는 것을 만들지 않는다

초대 **거절** 엔드포인트는 TEAM·PRJ 어디에도 없다. 초대자의 **취소**(`DELETE`)만 있다.

### 6.6 페이지네이션

**커서 기반**. `limit` 기본 20 / 최대 100. 오프셋은 명세 위반.

---

## 7. 스키마 규칙

- **raw SQL DDL이 정본이다.** `alembic/versions/0001_initial.py` 하나에 부분 인덱스·`lower()` UNIQUE·
  CHECK·트리거가 모두 들어 있다. autogenerate로는 만들 수 없다.
- **Alembic만 psycopg(동기)를 쓴다.** asyncpg는 한 execute에 여러 문장을 담지 못해 raw DDL이 깨진다.
  런타임은 asyncpg.
- `test_schema.py`가 매 실행마다 모델↔DB 컬럼 대칭과 트리거를 검사한다. 느슨하게 만들지 않는다.
- **소프트 삭제는 없다.** platform 스키마에 `deleted_at`이 없다.
  `projects.team_id`는 `ON DELETE RESTRICT`이고, 팀 삭제 409는 이 제약 위반을
  `TEAM_HAS_ACTIVE_PROJECTS`로 번역해 처리한다 (승인된 설계).
  **IntegrityError는 제약 이름 상수로 분기한다.** 메시지 문자열 파싱은 PG 마이너 버전에 깨진다.
- `teams.member_count`를 저장하지 않는다. 조회 시 집계한다 (동시 가입 경합).
- 37테이블 중 Phase 0에서 **쓰는** 것은 platform 11개뿐이다. 나머지 26개도 제약을 박아 이미 만들었다.
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

**다음 (TEAM·PRJ):**

1. 감사 보고서 먼저 → 승인 후 수정
2. `NOT_TEAM_MEMBER`, 팀 프로젝트 권한 완화, 멤버 목록 `username`, 초대 `token_hash`
3. 누락 엔드포인트: `GET /teams/{teamId}/projects`,
   `PATCH/DELETE /teams/{teamId}/members/{userId}`, PRJ 멤버 역할 변경·제거
4. PG16에서 전체 재실행 (compose 버전. 현재 dev·test 모두 PG15)

---

## 10. 반드시 추가할 테스트

현재 22 경로 · 33 오퍼레이션에 테스트 33개다 (`/v1/health` 제외).

- **만료 초대 수락** → 410 `INVITATION_EXPIRED`
- **유일 owner 강등/탈퇴** → 409 `LAST_OWNER_CANNOT_LEAVE`

리프레시 토큰 재사용 거부 · 테넌트 격리 · `signup_ticket` 오용 거부는 `f586427`에서 추가됨.

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
- 프론트엔드 계약 미대조. `# ASSUMPTION:` 주석으로 표시되어 있으나,
  어긋나면 Phase 0 전체를 손봐야 한다 (ROADMAP 113행)
