# ROADMAP — 인계 문서

다음 세션의 Claude Code가 읽는 파일이다. **계약은 [CLAUDE.md](CLAUDE.md)가 정본이고**,
이 파일은 "지금 어디까지 됐고, 뭐가 남았고, 뭐가 위험한지"만 기록한다.
둘이 충돌하면 CLAUDE.md가 이긴다.

작성: 2026-09-10 (Phase 0 완료 시점)

---

## 0. 세션 시작하면 이것부터

```bash
cd ~/Desktop/전공동
uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run lint-imports
DATABASE_URL="postgresql+asyncpg://kmsmss@localhost:5432/prolog_test" uv run pytest -q   # 28 passed
uv run uvicorn app.main:app --reload                                                     # http://localhost:8000/docs
```

이 5줄이 전부 통과하는 상태에서 인계했다. **통과하지 않으면 새 작업을 시작하기 전에 원인을 찾아라.**
`Address already in use` → `lsof -nP -tiTCP:8000 -sTCP:LISTEN | xargs kill`

- DB는 **로컬 PostgreSQL 15** (도커 데몬이 꺼져 있었다). dev DB `prolog`, 테스트 DB `prolog_test`, 유저 `kmsmss`, 비밀번호 없음
- `.env`는 있고 gitignore 대상이다. 없으면 `.env.example` 복사
- **이 디렉토리는 아직 git 레포가 아니다.** 커밋 이력이 0이다 (§11 "모듈/단계 단위 커밋"을 지키려면 `git init`부터)

---

## 1. 완료 — Phase 0

CLAUDE.md §13 Phase 0 완료 기준("인증된 사용자가 프로젝트를 만들고 동료를 초대해 수락까지 마친다")을
[tests/test_projects.py](tests/test_projects.py) `test_phase0_done_criteria`가 검증한다.

| 영역 | 상태 | 위치 |
| --- | --- | --- |
| 공통 규격 | 완료 | `app/core/` — config · security · deps · response · errors · pagination |
| 38테이블 스키마 | 완료 | `app/db/alembic/versions/0001_initial.py` (revision `0001`) |
| AUTH | 5 엔드포인트 | `app/platform_/auth/` |
| TEAM | 11 엔드포인트 | `app/platform_/teams/` |
| PRJ | 11 엔드포인트 | `app/platform_/projects/` |
| Outbox 쓰기 | 완료 (릴레이는 미완) | `app/events/outbox.py` |
| 계약 검사 | importlinter 5계약 + 스키마 정합성 3테스트 | `.importlinter`, `tests/test_schema.py` |

**핵심은 엔드포인트가 아니라 `core/`다.** `require_project_role(min_role)` 하나로 프로젝트 권한이 수렴하고,
앞으로 붙는 10개 모듈이 전부 이걸 쓴다. 응답 래퍼 · 에러 매퍼 · 커서 유틸도 여기 있다. **모듈마다 다시 만들지 마라.**

### 스키마는 38테이블 전부 만들어져 있다

Phase 0에서 **쓰는** 테이블은 12개(platform)뿐이지만, 나머지 26개도 §5의 제약을 박아서 이미 만들었다.
`chapters(manuscript_id, chapter_no)` UNIQUE, `characters` active 부분 UNIQUE,
`relationship_histories` 복합 PK + carry-forward 인덱스, `foreshadowing_chapters` setup/payoff 단일 보장,
`jobs` 상태 CHECK + 좀비 회수 부분 인덱스, `outbox_events` 부분 인덱스, `updated_at` 트리거 38개,
`manuscripts.source_type` 불변 트리거까지 들어 있다.

**즉, 새 모듈을 붙일 때 마이그레이션이 필요 없다.** 모델도 이미 있다 — `router.py` · `schemas.py` ·
`service.py` · `repository.py` 4개만 쓰면 된다.

---

## 2. 남은 것

### Phase 1 — MSU · 챕터 · io 워커 · Outbox 릴레이 · NOTI  ✅ **완료**

만든 것 (아래 목록대로, 4번 제외):
1. `content/manuscripts/` 의 router·schemas·service·repository
   - `POST /v1/projects/{projectId}/manuscripts` (`source_type` = `editor` | `upload`)
   - upload는 S3 presigned URL 발급 → 클라이언트 직접 업로드 → 추출 잡 생성
   - 챕터 CRUD (`chapters.manuscript_id`가 NOT NULL이다. project 직결이 아니다)
   - 자동저장 PATCH — 본문은 PostgreSQL `text`다. S3 왕복하지 마라 (§9)
2. **Outbox 릴레이 워커** — `published_at IS NULL` 폴링 → SQS → `published_at` 기록.
   `FOR UPDATE SKIP LOCKED` 라 릴레이를 여러 개 띄워도 같은 행이 두 번 가지 않는다.
   **at-least-once** 다 — 발송 후 표시 사이에 죽으면 재전송되고, 수신 측이 멱등해야 한다

   **큐는 3종이다. 하나가 아니다.**

   | 큐 | 소비자 | 싣는 것 |
   | --- | --- | --- |
   | `io` | `worker/extractor.py` | `queue='io'` 잡 (원고 텍스트 추출) |
   | `ai` | (Phase 3) | `queue='ai'` 잡 |
   | `notify` | `worker/notifier.py` | 도메인 이벤트 (초대 등) |

   **라우팅 규칙** (`app/events/relay.py` 의 `route()`):
   `aggregate_type == 'job'` 이면 이벤트 payload 의 `queue` 값(`io`·`ai`)으로,
   그 외 도메인 이벤트는 전부 `notify` 로 보낸다.

   한 큐를 쓰면 안 된다 — SQS 는 메시지를 **한 소비자에게만** 주므로 notifier 와 extractor 를
   같은 큐에 붙이면 notifier 가 잡 메시지를 받아 무시하고 삭제해 버린다. extractor 는 굶는다.
3. **NOTI** — 릴레이가 보낸 이벤트를 받아 `notifications` INSERT.
   `channels_sent`는 설정값이 아니라 **실제 발송 결과**다 (메일 실패 시 `{in_app}`만)
4. `manuscript_versions` 디바운스 — **§12-3 미결.** 정하지 말고 물어라

⚠️ **순서 충돌 발견**: §13은 "텍스트 추출 잡"을 Phase 1에, "jobs 인프라"를 Phase 2에 둔다.
추출 잡을 만들려면 `jobs` 최소 코어(생성 · 상태 전이 · 폴링 응답 + `meta.retry_after_ms`)가 먼저 필요하다.
**권고**: Phase 1에서 jobs 최소 코어만 만들고, 재시도 · 좀비 회수 · DLQ · 멱등키는 Phase 2에서 얹는다.
`ops.jobs` 테이블과 모델은 이미 완성돼 있으니 코드만 쓰면 된다.

**docker-compose 현황:** postgres(16) · redis · **elasticmq**(SQS 호환, 9324) ·
**s3mock**(S3 호환, 9000). localstack 이 아니라 elasticmq 인 이유는 가볍고 SQS API 가 같아
AWS 로 옮길 때 코드가 바뀌지 않기 때문이다. minio 가 아니라 s3mock 인 이유는
`minio/minio` 와 `quay.io/minio/minio` 모두 pull 이 거부되기 때문이다(2025 배포 정책 변경).
**Redis 는 아직 아무도 안 쓴다.**

### Phase 2 — 잡 인프라 완성 (분수령)

REX 하나로 생성 · 폴링 · 실패 · 재시도 · 확정 전 경로를 끝까지 검증한다.
모듈이 실제로 쓰는 코드는 4개뿐이어야 한다: `build_input` · `prompt_template` · `parse_result` · `confirm`.
§6의 장치 3가지(조건부 UPDATE 중복 실행 방지 · 좀비 회수 · 재시도 계약)를 빠뜨리지 마라.
**여기를 대충 넘기면 모듈마다 다른 방식으로 실패한다.**

### Phase 3 — NLCD · ASS · SSM · AIQ

모듈당 `job_handler.py` 1개 + 라우터로 끝나야 한다. 그렇지 않으면 Phase 2가 덜 된 것이다.
필드명은 ASS 기준이 정본(`personality_tags` / `core_values` / `influence_relations` / `emotion_keywords`).
SCDS 명세의 `traits` / `values` / `influences`는 **응답 직렬화 계층의 별칭으로만** 만든다.

### Phase 4 — SCDS · RCV · FTS

- 룰 엔진은 인메모리 + Redis 캐시. **100ms 넘기면 안 된다** (사용자가 글 쓰는 도중 타는 경로)
- `suppression_key`의 `normalize()` 범위는 **§12-1 미결** — 함수 하나로 분리하고 TODO를 남길 것
- RCV carry-forward는 앱 루프가 아니라 §9의 `DISTINCT ON` SQL로 푼다

### Phase 5 — Outbox 확장 · SSE 준비 · 읽기 캐시

---

## 3. 문제 — 우선순위 순

### P1. 명세 13종과 대조하지 않았다 (가장 큰 리스크)

AUTH 경로 · 필드명 · 에러 코드 14종을 **발명했다.** 명세 원문을 참조하지 못했다.
`# ASSUMPTION:` 주석으로 표시해 뒀지만, 프론트엔드와 계약이 어긋나면 Phase 0 전체를 손봐야 한다.

```bash
grep -rn "ASSUMPTION" app    # 15곳
```

발명한 것: `/v1/auth/signup|login|refresh|logout|me` 경로, `VALIDATION_ERROR` ·
`UNAUTHORIZED` · `FORBIDDEN` · `INVALID_CREDENTIALS` · `INVALID_REFRESH_TOKEN` ·
`EMAIL_ALREADY_EXISTS` · `DUPLICATE_INVITATION` · `INVITATION_NOT_PENDING` ·
`INVITATION_EMAIL_MISMATCH` · `LAST_OWNER_CANNOT_LEAVE` · `MEMBER_NOT_FOUND` ·
`INVALID_OWNER_TYPE` · `PROJECT_NOT_FOUND` · `TEAM_NOT_FOUND` 등.
**명세를 확보하는 즉시 `app/core/errors.py`와 라우터 경로를 대조하라.** 지금이 가장 싸다.

같은 이유로 **38테이블 중 26개(content·authoring·insight)의 컬럼 구성도 ASSUMPTION이다.**
아직 코드가 안 붙었으니 고치기 가장 쉬운 시점이 지금이다.

### P2. 규칙 3 때문에 멤버 목록에 사람 이름이 없다 — 결정 필요

`GET /teams/{id}/members`와 `GET /projects/{id}/members`가 `user_id`만 돌려준다.
`platform.users`는 AUTH 소유이고 같은 Ring 안의 동기 호출이 금지(규칙 3)라서 nickname·email을 못 붙인다.
프론트가 UUID만 받는다는 뜻이고, 실사용이 불가능하다.

선택지 3개 — **임의로 정하지 말고 물어라. §12에 5번 항목으로 추가할 것을 권한다.**
1. `user.updated` 이벤트로 멤버 테이블에 표시용 필드를 비정규화 (규칙 준수, ERD에 컬럼 추가)
2. `users`를 `app/domain/`의 공유 커널로 승격 (규칙 3 예외를 명시적으로 인정)
3. `core/deps.py`처럼 읽기 전용 projection을 core에 두기 (예외 지점이 늘어난다)

### P3. 초대를 받는 사람이 초대를 볼 방법이 없다

메일 발송이 Phase 1(NOTI)이고, "내게 온 초대 목록" API도 없다.
지금은 초대 생성 응답의 `invitation_id`를 사람이 직접 전달해야 수락할 수 있다.
Phase 1에서 NOTI를 붙일 때 같이 해결하라. 수락은 `invited_email == JWT의 email` 매칭이다(토큰 링크 없음).

### P4. 규칙 대비 의도적 편차 2건 (승인 대기)

1. **팀 삭제 409를 DB 제약으로 처리했다.** §5는 앱 레벨 사전 검증을 요구하지만, 프로젝트 존재 확인은
   PRJ 테이블 읽기 = 같은 Ring 동기 호출이다. `projects.team_id` RESTRICT 위반을
   `TEAM_HAS_ACTIVE_PROJECTS`로 번역했다 ([teams/service.py](app/platform_/teams/service.py))
2. **`core/deps.py`가 platform 테이블을 raw SQL로 읽는다.** core가 모듈 repository를 import하면
   의존 방향이 뒤집힌다. `require_project_role` · `require_team_role` · `assert_team_role`이
   멤버십 테이블을 읽는 **유일한 예외 지점**이다. 여기 말고 다른 곳에서 늘리지 마라

### P5. 미구현 (설계는 되어 있음)

- 소셜 로그인 — `users.auth_provider` CHECK에 `google`·`kakao`·`naver`가 있지만 코드가 없다
- 비밀번호 재설정 — `password_resets` 테이블만 있다. 메일 발송이 Phase 1이라 엔드포인트를 만들지 않았다
- 권한 Redis 캐시 — §9는 권한을 Redis에 두라고 한다. 현재는 요청마다 인덱스 조회 1회. 측정 후 판단
- 액세스 토큰 즉시 폐기 불가 — JWT만 검증하고 DB를 안 친다(`core/deps.py`의 `ponytail:` 주석 참조)
- 팀 프로젝트를 만들어도 팀 멤버가 `project_members`로 자동 승격되지 않는다(생성자만). 의도한 결정이다

### P6. 환경

- **로컬 검증이 PostgreSQL 15다.** compose는 16이고 쓴 기능은 전부 13+ 호환이지만, 16에서 한 번 더 돌려야 한다
- git 미초기화 — 커밋이 0이다

---

## 4. 다음 세션이 밟을 지뢰

1. **Alembic만 동기 psycopg를 쓴다.** asyncpg는 한 execute에 여러 문장을 못 담아서 raw DDL이 깨진다.
   `env.py`가 URL의 `+asyncpg`를 벗겨낸다. 런타임(app)은 asyncpg 그대로다
2. **DDL 정본은 마이그레이션 파일의 raw SQL이다.** 모델에는 컬럼만 있고 CHECK·부분 인덱스·트리거가 없다.
   `alembic revision --autogenerate`를 돌리면 **생성물을 반드시 읽고 고쳐라**.
   모델↔DB 어긋남은 `tests/test_schema.py`가 잡는다 (38테이블 · 컬럼 대칭 · 트리거 38개)
3. **DDL에 `%`를 쓰지 마라.** 드라이버 paramstyle과 충돌한다. `format('%I')` 대신 `quote_ident()`를 썼다
4. **pytest는 세션 단일 이벤트 루프다** (`asyncio_default_*_loop_scope = "session"`).
   커넥션 풀이 테스트마다 새 루프에 묶이면 `attached to a different loop`로 죽는다
5. **테스트는 실제 Postgres다.** SQLite로 바꾸지 마라 — 부분 인덱스·jsonb·`text[]`·`DISTINCT ON`이 죽는다.
   `clean_tables` fixture가 매 테스트마다 5개 스키마를 TRUNCATE한다
6. **요청 1개 = 트랜잭션 1개** (`app/db/session.py`가 커밋한다). Outbox INSERT가 도메인 변경과
   같은 트랜잭션에 묶이는 게 이 구조 덕분이다. 서비스에서 `commit()`을 부르지 마라
7. **`role` 컬럼은 3개다.** `users.role`(자기 분류, **인가와 무관**) · `project_members.role` ·
   `team_members.role`. `require_project_role`이 보는 건 `project_members.role`뿐이다
8. `dev` DB(`prolog`)에 부팅 확인용 `smoke@example.com` 한 줄이 남아 있다

---

## 5. §12 미해결 — 임의로 정하지 말 것

CLAUDE.md §12 원본 4건은 그대로 남아 있다. 손대지 않았다.

1. `suppression_key`의 `normalize()` 범위 (공백·조사·대소문자) — Phase 4에서 필요
2. SSM 재분석 시 `is_user_edited = true` 노드의 병합 정책 — Phase 3에서 필요
3. `manuscript_versions` 디바운스 기준 — **Phase 1에서 필요. 가장 먼저 물어야 한다**
4. AIQ `scope=selection`의 오프셋 무효화 (`selected_text` 스냅샷 여부) — 컬럼은 미리 만들어 뒀다

여기에 P2(멤버 표시 데이터)를 5번으로 추가할 것을 권한다.
