# CLAUDE.md — Prolog / StoryForge Backend

> **이 문서의 상태.** 이 파일은 기존 CLAUDE.md를 **대체하기 위해 새로 작성된 초안**이다.
> 작성 시점에 기존 CLAUDE.md 본문을 확인할 수 없었으므로, 원본의 §5·§9·§11·§12·§13 등
> 조항 원문은 여기에 반영되지 않았다. **기존 파일을 덮어쓰기 전에 아래 "병합 필요" 절을 확인하라.**

## 병합 필요

기존 CLAUDE.md에만 있고 이 문서에 없을 수 있는 것:

- Ring 구조와 "규칙 3"의 정확한 원문 (여기서는 요약된 형태로만 기술)
- §9 (권한 캐시), §11 (커밋 규칙), §12 (미결 항목 목록), §13 (Phase 정의)
- import-linter 계약 5종의 원문 정의
- 코딩 컨벤션, 네이밍 규칙, 디렉터리 구조 규정

**지시:** 이 문서와 기존 파일을 나란히 놓고, 위 항목은 기존 파일에서 가져와 합쳐라.
충돌하는 부분은 이 문서를 우선한다 (AUTH 계약이 v0.2로 바뀌었기 때문).

---

## 1. 프로젝트

| 항목 | 값 |
| --- | --- |
| 서비스 | StoryForge — AI 기반 스토리 구조 관리 IDE |
| 레포 경로 | `~/Desktop/전공동` |
| 스택 | FastAPI · PostgreSQL · SQLAlchemy(asyncpg) · Alembic(psycopg) · uv |
| 현재 단계 | Phase 0 — AUTH 계약 정렬 중 |

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

1. **`.env`를 커밋하지 않는다.** `git init` 전에 `.gitignore`에 `.env`가 있는지 확인한다.
   JWT 서명 키와 Google client secret이 들어 있다. 히스토리에 한 번 들어가면 rewrite 없이 제거할 수 없다.
2. **테스트는 `_test`로 끝나는 DB에서만 돈다.** `conftest.py`의 `pytest_configure` 가드를 제거하지 않는다.
   가드 없이 `DATABASE_URL`을 빠뜨리면 `.env`가 로드되어 dev DB를 지운다.
3. **`TRUNCATE ... CASCADE`를 dev DB에 쓰지 않는다.** 38테이블 5스키마에서 참조 체인 전체가 비워진다.
   특정 행만 지울 때는 `DELETE FROM ... WHERE`를 쓴다.
4. **비밀번호를 저장하지 않는다.** Google OAuth 단일이다. bcrypt/argon2 코드를 되살리지 않는다.

---

## 3. 명세 정본

명세는 **Notion**에 있다. 코드와 어긋나면 **Notion이 정본**이다.

| 문서 | 위치 |
| --- | --- |
| 폴더 | `prolog API 명세 정리` |
| AUTH | `로그인/회원가입 기능 API 명세 (AUTH)` — **v0.2, Google 단일** |
| TEAM | `팀 생성 기능 API 명세 (TEAM)` |
| PRJ | `프로젝트 생성 기능 API 명세 (PRJ)` |
| ERD | `🗂️ Prolog 최종 아키텍쳐 / 01. platform` (Ring 1) |
| 아키텍처 | `🏗️ Prolog 백엔드 아키텍처 설계서` |

**규칙:** 명세에 없는 것을 발명하지 않는다. 발명이 불가피하면 코드에 `ASSUMPTION:` 주석을 남기고
보고 시 별도 목록으로 제시한다. **명세를 읽지 않은 채로 엔드포인트를 구현하지 않는다.**

명세 자체가 틀렸다고 판단되면 코드를 먼저 고치지 말고 **Notion을 먼저 고치자고 제안**한다.
정본이 둘이 되는 상황을 만들지 않는다.

> ⚠️ **AUTH 페이지 현재 상태 주의.** Notion AUTH 문서는 v0.2 전환 중 **일부만 수정된 상태**다.
> 공통사항·에러코드·데이터모델은 v0.2로 갱신되었으나, 엔드포인트 목록(§3)과 상세 명세(§4)에는
> 아직 로컬 가입/로그인·네이버·비밀번호 재설정 서술이 남아 있다.
> **§3·§4를 읽을 때는 아래 4절을 우선하라.** 문서 정리가 끝나면 이 경고를 삭제한다.

---

## 4. AUTH 계약 — v0.2 (Google 단일)

### 4.1 엔드포인트

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
사용자 리소스는 `/users` 아래, 인증 동작만 `/auth` 아래에 둔다.

### 4.2 2단계 가입 흐름

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

### 4.3 필수 구현 세부

- **`provider_user_id`에는 Google `sub`를 넣는다.** 이메일이 아니다.
  이메일은 변경 가능하고 `sub`는 불변이다. 기존 사용자 판별은 `(auth_provider, provider_user_id)` 조회로 한다.
- ID 토큰은 **서명·`aud`·`iss`·`exp`를 모두 검증**한다.
- `username`(varchar 30, NOT NULL, UNIQUE)과 `role`(`writer`/`aspiring_writer`/`reader`)은
  Google이 주지 않는다. 가입 완료 화면에서 사용자가 선택한다.
- 리프레시 토큰 **로테이션 재사용은 거부**한다. 폐기된 토큰 재제출 → `REFRESH_TOKEN_INVALID`.

### 4.4 Google 검증을 포트로 분리한다

ID 토큰 검증과 code 교환을 **인터페이스로 추상화**하고, 테스트에서 fake 구현을 주입한다.

- 테스트가 Google을 실제로 호출하면 안 된다.
- 로컬 가입 엔드포인트를 테스트 편의용으로 되살리지 않는다. 인증 경로가 둘이 되면
  설정 실수 하나로 비밀번호 계정 생성 경로가 열린다.
- fake 주입 방식이면 테스트가 **실제 프로덕션 경로를 그대로 통과**한다.
- dev 계정이 필요하면 seed 스크립트로 행을 직접 넣는다.

### 4.5 AUTH 에러 코드

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
**존재하지 않음:** `VALIDATION_ERROR` — 명세는 `INVALID_INPUT`이다. AUTH에는 `FORBIDDEN`이 없다.

---

## 5. TEAM · PRJ 계약 주의점

명세를 직접 읽고 구현하되, 이전 구현이 틀렸던 지점만 명시한다.

### 5.1 에러 코드는 모듈별로 이름이 다르다

| 상황 | TEAM | PRJ |
| --- | --- | --- |
| 멤버 없음 | `TEAM_MEMBER_NOT_FOUND` | `MEMBER_NOT_FOUND` |
| 초대 없음 | `TEAM_INVITATION_NOT_FOUND` | `INVITATION_NOT_FOUND` |
| 이미 멤버 | `ALREADY_TEAM_MEMBER` | `ALREADY_MEMBER` |

공통 처리로 뭉개지 않는다. 특이 상태 코드: **`INVITATION_EXPIRED`는 410**,
`LAST_OWNER_CANNOT_LEAVE`는 409(TEAM·PRJ 공통), `NOT_TEAM_MEMBER`는 403(PRJ 전용).

### 5.2 팀 프로젝트 생성 권한

`owner_type=team`일 때 요구되는 것은 **팀 소속 여부뿐**이다. 팀원 누구나 만들 수 있다.
`owner|admin`으로 제한하지 않는다. 비소속자는 `NOT_TEAM_MEMBER`(403) — 일반 `FORBIDDEN` 아님.

### 5.3 멤버 목록에 `username`을 포함한다

TEAM 4.6·PRJ 4.6 모두 응답에 "(사용자 이름 포함)"을 명시한다. `user_id`만 반환하면 명세 위반이며
프론트가 멤버 화면을 렌더할 수 없다.

필요한 것은 `username` 하나다(이메일 아님). 구현 방법 우선순위:

1. **상위 조합 레이어에서 합친다** — 라우터/조합 레이어는 모듈 위에 있으므로 AUTH 조회와
   TEAM 멤버 목록을 각각 호출해 응답에서 합쳐도 모듈→모듈 의존이 아니다. 가장 싸다.
2 outbox 이벤트로 `username`만 비정규화.
3. `users`를 공유 커널로 승격 — 되돌리기 가장 어렵다. 마지막 수단.

### 5.4 초대는 `token_hash` 링크로 처리한다

`invited_email == JWT.email` 매칭을 쓰지 않는다. ERD의 `team_invitations`·`project_invitations`에
**`token_hash` UK 컬럼이 이미 있다.** 초대받은 주소와 Google 로그인 주소가 다른 경우가 흔하다
(회사 메일로 초대, 개인 지메일로 로그인).

누락되기 쉬운 컬럼: `token_hash`, `invited_by` FK, `accepted_at`.

### 5.5 명세에 없는 것을 만들지 않는다

초대 **거절** 엔드포인트는 TEAM·PRJ 어디에도 없다. 초대자의 **취소**(`DELETE`)만 있다.
이전 구현이 추가한 거절 엔드포인트는 계약 외 표면이다.

### 5.6 페이지네이션

**커서 기반**. `limit` 기본 20 / 최대 100, `cursor`. 오프셋 방식은 명세 위반.

---

## 6. 스키마 규칙

- **raw SQL DDL이 정본이다.** `alembic/versions/0001_initial.py` 하나에 부분 인덱스·`lower()` UNIQUE·
  CHECK·트리거가 모두 들어 있다. autogenerate로는 이들을 만들 수 없다.
- **Alembic만 psycopg(동기)를 쓴다.** asyncpg는 한 execute에 여러 문장을 담지 못해 raw DDL이 깨진다.
  런타임은 asyncpg 그대로.
- `test_schema.py`가 매 실행마다 모델↔DB 컬럼 대칭과 트리거를 검사한다. 이 테스트를 느슨하게 만들지 않는다.
  단, `core/deps.py`의 원시 SQL 문자열은 이 대칭 검사 바깥이다 — 컬럼 리네임 시 직접 확인한다.
- **소프트 삭제는 없다.** platform 스키마에 `deleted_at`이 없다. `projects.team_id`는 `ON DELETE RESTRICT`이고,
  팀 삭제 409는 이 제약 위반을 `TEAM_HAS_ACTIVE_PROJECTS`로 번역해 처리한다 (승인된 설계).
  **IntegrityError는 제약 이름 상수로 분기한다.** 메시지 문자열 파싱은 PG 마이너 버전에 깨진다.
  DDL에서 FK에 이름을 명시적으로 부여하고 그 상수를 참조한다.
- `teams.member_count`를 저장하지 않는다. 조회 시 집계한다 (동시 가입 경합으로 카운터가 틀어진다).

### v0.2 스키마 변경 (아직 커밋 전이면 `0001_initial.py`를 직접 수정)

- `password_resets` 테이블 삭제
- `users.password_hash` 컬럼 삭제
- `users.provider_user_id` → NOT NULL
- `auth_provider` CHECK → `IN ('google')`
- `CHECK (auth_provider <> 'local' OR password_hash IS NOT NULL)` 삭제
- `UNIQUE (auth_provider, provider_user_id)` → 부분 조건 없이 UNIQUE
- `UNIQUE (lower(email)) WHERE email IS NOT NULL` 유지

> 레포가 아직 git으로 초기화되지 않았고 마이그레이션이 커밋되지 않았다면
> `0001_initial.py`를 직접 고쳐도 체인이 지저분해지지 않는다. **이 자유는 첫 커밋과 함께 사라진다.**

---

## 7. 아키텍처 규칙

- 단일 FastAPI 앱 + 별도 워커. 13개 모듈은 코드 레벨에서만 갈라진다.
- **모듈 → 모듈 직접 import 금지** (규칙 3). 같은 Ring 동기 호출도 금지.
  상위 조합 레이어 → 하위 모듈은 허용된다.
- `core`는 모듈 repository를 import하지 않는다. 방향이 뒤집힌다.
- **승인된 예외:** `core/deps.py`가 platform 멤버십 테이블을 원시 SQL로 읽는다
  (`require_project_role` / `require_team_role` / `assert_team_role`).
  이 예외를 두 번째로 늘리지 않는다. 늘려야 할 상황이면 `platform`을 **명시적 레이어로 승격**해
  import-linter가 검증할 수 있는 정상 관계로 만들자고 제안한다.
- import-linter 계약을 통과하지 못하는 코드를 커밋하지 않는다. 계약을 느슨하게 고쳐서 통과시키지 않는다.

---

## 8. 지금 할 일 (순서 고정)

1. `.gitignore` 작성 → `git init` → `.env` 제외 확인. `.env.example`도 만든다.
2. `conftest.py`에 `pytest_configure` DB 가드 추가.
3. `0001_initial.py`를 §6 v0.2 목록대로 수정.
4. Google ID 토큰 검증 포트 + fake 구현. `core/security`에서 bcrypt 제거.
5. `/auth/oauth/google` + `signup_ticket`, `/auth/signup` 재작성, `/users/check-username` 추가.
6. 경로 정정 (`/auth/token/refresh`, `/users/me`) + `PATCH /users/me`.
7. 에러 코드 이름 정렬 (§4.5, §5.1).
8. 테스트 픽스처를 fake 토큰 기반으로 전환.

그 다음 (AUTH 정리 후):

9. `NOT_TEAM_MEMBER`, 팀 프로젝트 권한 완화, 멤버 목록 `username`, 초대 `token_hash`.
10. 누락 엔드포인트: `GET /teams/{teamId}/projects`, `PATCH/DELETE /teams/{teamId}/members/{userId}`,
    PRJ 멤버 역할 변경·제거.
11. PG16에서 전체 테스트 재실행 (compose 버전. 현재 dev·test 모두 PG15).

---

## 9. 반드시 추가할 테스트

현재 22 엔드포인트에 28 테스트로 얇다. 최소한 다음은 있어야 한다.

- **리프레시 토큰 재사용 거부** — 로테이션했으면 폐기 토큰 재제출이 401이어야 한다. 보안 위험이 가장 큰 경로.
- **테넌트 격리** — 사용자 A가 사용자 B의 팀/프로젝트를 읽지 못한다.
- **`signup_ticket` 오용 거부** — 티켓을 `Authorization: Bearer`로 제출하면 401.
- **만료 초대 수락** → 410 `INVITATION_EXPIRED`.
- **유일 owner 강등/탈퇴** → 409 `LAST_OWNER_CANNOT_LEAVE`.

---

## 10. 보고 형식

작업 완료 시 다음을 구분해 보고한다.

1. **게이트 결과** — ruff / format / mypy / lint-imports / pytest
2. **만든 것** — 범위별
3. **명세 편차** — 명세와 다르게 구현한 것. 승인 요청 형태로.
4. **ASSUMPTION** — 명세에 없어서 발명한 것. 근거와 함께.
5. **일부러 안 만든 것** — 이유와 함께.
6. **확인 필요** — 검증되지 않은 것 (환경 차이, 미실행 테스트 등)

"완료"를 선언할 때 **무엇에 대한 완료인지** 명시한다. 명세와 대조되지 않은 구현은 완료가 아니다.

---

## 11. 미결 항목

- 권한 Redis 캐시. ERD가 `(project_id, user_id) → role`을 **60초 TTL 캐싱, 멤버 변경 시 무효화**로
  규정했다. Phase 1 백로그. 필요한 인덱스 `project_members(user_id)`는 이미 있다.
- outbox 릴레이 워커 (Phase 1)
- Celery (Phase 2)
- 비멤버 접근 시 403 vs 404. 명세는 403(존재 노출)이고 구현도 403이다.
  비공개 세계관을 다루는 도구라 404 은닉이 어울릴 여지가 있으나, 바꾸려면 명세부터 고친다.
- 기존 CLAUDE.md §12의 미결 4건 — 이 문서 작성 시 원문을 확인하지 못했다. 병합 시 가져올 것.
