# Prolog (StoryForge) — 백엔드

AI 기반 스토리 구조 관리 IDE 의 백엔드. FastAPI · PostgreSQL 16 · SQLAlchemy(asyncpg) · uv.

이 파일은 **실행 방법**만 적는다. API 계약은 `openapi.json` 이다.

## 필요한 것

- Docker (compose v2)
- [uv](https://docs.astral.sh/uv/) · Python 3.12

## 시작하기

```bash
git clone https://github.com/BSSM-Pitch/prolog-backend.git
cd prolog-backend

docker compose up -d          # postgres · redis · elasticmq(SQS) · s3mock(S3)
uv sync
cp .env.example .env          # JWT_SECRET 을 채운다 (아래 명령 출력)
python -c "import secrets; print(secrets.token_urlsafe(48))"
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
uv run python -m worker.ai_worker      # AI 잡(자연어 추출 등). API 키 없이 돌리려면 USE_FAKE_LLM=1
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

## 원고별 인물 디테일과 수치

`alembic upgrade head`(0016) 후 API와 `worker.ai_worker`, `worker.outbox_relay`를 실행한다.
실제 분석은 `NVIDIA_API_KEY`, `PROLOG_AI_PROVIDER=nvidia`, `PROLOG_AI_MODEL=z-ai/glm-5.3-flash`,
`PROLOG_AI_REASONING_EFFORT=low`를 사용한다. 키는 서버 환경에만 설정한다.

공통 경로: `/v1/projects/{projectId}/manuscripts/{manuscriptId}`.

| 메서드·경로 | 기능·권한 |
| --- | --- |
| `GET /character-memory` | 최신 작업 진행 상태·완료본 ID·원고 변경 여부, viewer |
| `POST /character-memory` | 인물 탐색·자동 등록 후 원고 기억 분석 요청 (등록 인물 0명도 가능), editor |
| `POST /character-memory/{analysisId}/retry` | 실패 작업의 체크포인트에서 재개, editor |
| `POST /character-memory/{analysisId}/candidates/{candidateId}/resolve` | 보류한 이름을 `create`·`link`(character_id 필요)·`dismiss`로 확인, editor |
| `GET /characters/{characterId}/details?through_order=3` | 해당 시점까지의 관찰·수치·지지/반례·잠정 해석, viewer |
| `GET /characters/{characterId}/assessment?through_order=3` | 공통 행동 기준과 저장된 인물 평가·진행 상태, viewer |
| `POST /characters/{characterId}/assessment?through_order=3` | 해당 시점까지의 인물 평가 요청, editor |

장편 분석은 수시간이 걸릴 수 있다. 요청에서는 모델을 호출하지 않으며 진행 중/동일 완료본을
재사용한다. 워커는 최대 10개 모델 호출 단위로 기억과 호출 캐시를 `ops.jobs.result`에 저장한다.
실패해도 체크포인트를 보존하고, 재시도는 같은 원고 스냅샷에서만 가능하다. 원고나 인물이 바뀌면
새 분석을 요청한다. 기존 완료본은 변경 표시와 함께 계속 읽을 수 있다. 원문이 없는 장은 제외한다.

분석의 첫 단계는 전체 원고의 인물 탐색이다. 12,000자 단위(300자 겹침)로 모든 구간을 읽고,
이름 초안을 원문과 다시 대조해 정식 이름 누락·축약 오류를 검토하고, 이름을 포함한 근거 구절이
원문과 일치하는지 Unicode 좌표로 검증한다. 이름이 특정된 인물은
자동 등록하고, 기존 이름·작가가 확인한 별칭은 재사용한다. 다른 정식 이름의 일부인 표기,
호칭, 별칭 추측, 동명 인물은 자동으로 합치거나 추가하지 않고 `needs_review`로 보류한다.
작가가 `link`하면 `authoring.characters.aliases`에 저장되며 다음 기억 분석에 전달된다.
AI의 인물 분류 자체는 오류가 있을 수 있다. 후보와 근거·처리 상태는 작업의 `discovery`에 남는다.
인물을 찾지 못하면 성공으로 종료하고 빈 기억이나 점수를 꾸며 만들지 않는다.

탐색은 구간별로 체크포인트를 저장한다. 전체 탐색이 끝난 뒤 원고 변경 여부와 작업 attempt를
다시 확인하고, 등록·인물 목록 갱신·기억 입력 스냅샷 확정을 한 트랜잭션에서 수행한다.
수동 확정과 같은 이름 잠금을 사용해 동시 분석의 중복 등록을 막는다. 보류 후보 확인은 분석
완료/실패 후 가능하며, 별칭 연결·추가 후에는 새 스냅샷으로 다시 분석한다. 업로드의 텍스트 추출만으로
모델을 호출하지는 않는다. 원고 목록이나 등장인물 화면의 ‘원고 분석 · 인물 자동 추가’로 시작한다.

수치는 **추출된 기록의 집계**다. 최근 3개 비어 있지 않은 장(장 미분리 원고는 1개 구간)과 앞선
3개를 비교한다. 선택 비중은 `선택 / (선택 + 비의도적 반응) × 100`이며 분모가 없으면 null이다.
두 기간 모두 3개 구간과 분모가 있을 때만 %p 차이를 낸다. 같은 원문 위치·역할은 한 번만 세며,
불확실한 화자는 제외한다. 상대별 수는 interlocutor가 명시된 관찰 수이며 친밀도나 고립 점수가
아니다. 5건 미만 경고는 표본 주의 표시이고 통계적 신뢰도 계산이 아니다.

반복 경향과 반례는 검토 범위 이후 시점에서는 보이지만, 그 이전 시점에서는 통째로 숨긴다.
심리 가설에는 대안 설명을 함께 제공한다. 원문 인용은 Unicode 코드 포인트 좌표로 검증한다.
AI 분류 자체의 정확도와 추출의 완전성을 보장하지 않는다. 현재 저장 방식은 원고 전체와
체크포인트 JSONB이므로 대규모 동시 분석을 운영하려면 저장량·읽기 응답 크기를 따로 측정해야 한다.

검증: `tests/test_character_memory.py`는 시간 범위, 중복·분모, 원문 좌표, 권한·프로젝트 격리,
스냅샷 변경, 재개와 작업 attempt fencing을 검증한다. 실제 모델 품질 평가는 별도다.

### 인물 자체의 평가

`character_assessment` 작업은 성향(주도성·감정 조절), 판단력(상황 판단·타인 이해),
역량(문제 해결·학습과 수정)을 각각 행동 기준 1–5단계로 평가한다. 기준은
`app/ai/character_assessment.py`의 `character-anchored-v1`에 버전으로 고정한다.
작품 전체를 통틀어 검증된 성격 검사나 인격 총점은 아니다. 강요된 선택, 결과 편향,
자기 주장, 상황 차이를 평가 근거와 구분한다.

요청 시 완료된 인물 기억에서 해당 인물의 확실한 기록만 선택하며, 시점 이후의 기록·경향은
입력에서 제외한다. 장면별 반례와 행동 역할을 우선하는 순환 표집으로 최대 180개 기록,
직렬화된 기록 합계 120,000자까지 입력한다. 각 인용 앞뒤의 실제 원문 80자와 맥락을 포함한다.
장면 수가 기록 예산보다 많으면 처음부터 마지막까지 균등한 간격으로 장면을 선택한다.
원고 전체를 직접 읽거나 모든 기록을 검토한 평가가 아니며, 검토/전체 기록·장면 수를 반환한다.
현재 기억의 `scene`은 장 단위이므로 장 미분리 원고는 점수 산출에 필요한 여러 구간이 없다.

같은 모델로 초안 생성과 비판적 재검토를 순차 실행한다(호출별 240초, 최대 1회 재시도).
인용 검증 오류는 검토 프롬프트에 전달한다. 검토본에도 인용 오류가 남으면 최대 한 번의 수정
호출(240초, 전송 재시도 없음)을 추가한다. 그래도 검증되지 않은 항목은 `unverified_evidence`로
점수·모델 해석·인용을 전부 보류하고, 다른 검증된 항목을 보존한다. 항목 누락·중복이나 응답
구조 오류는 작업 실패로 처리한다. 부분 검증 결과는 같은 POST로 다시 평가할 수 있다.
두 패스는 독립 평가자 일치도나 정확도 검증을 의미하지 않는다. 서버는 여섯 축, 인용 ID,
장면 소속, 중복, 자기 주장만으로 이루어진 평가를 검사하며, 오류가 있는 항목의 점수는 저장하지 않는다.
모델에는 짧은 인용 별칭을 제공하고 저장 전에 원래 ID로 엄격하게 복원한다.
항목별 최대 6개 장면의 1–5단계 중 중앙값을 점수로 표시한다. 서로 다른 장면이 3개 미만이면
점수는 null이다. 이 임계치는 표시 기준이며 통계적 신뢰도의 보장이 아니다. 관찰 범위의
최솟값·최댓값도 신뢰구간이 아니다. 강점·약점·반례 선택과 채점에는 모델의 해석이 남는다.

평가는 기억 분석 ID·인물·시점·기준 버전별로 저장/재사용한다. 원고가 바뀌었으면 기억을
갱신해야 새 평가를 요청할 수 있다. 실패한 평가는 같은 POST로 다시 요청한다. 허위 점수를
만들지 않도록 `USE_FAKE_LLM=1`에서는 평가를 거절한다. 근거가 3개 장면 미만이면 모델 호출 없이
판단 보류로 완료한다. `tests/test_character_assessment.py`가 집계·인용·표집·권한·캐시를 검증한다.

## 현재 상태

**Phase 1 완료.** AUTH(Google 단일) · TEAM · PRJ · MSU(원고 · 챕터) · NOTI(인앱) ·
Outbox 릴레이 · 잡 최소 코어까지 동작한다. 다음은 Phase 2 (잡 인프라 — 재시도 · 좀비 회수 ·
DLQ · 멱등키).
