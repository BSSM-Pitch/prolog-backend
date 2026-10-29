from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 예전 기본값이자 .env.example 에 공개됐던 값. 이 값을 복사해 쓴 env 도 기동을 막는다.
_PUBLIC_JWT_SECRET = "dev-secret-change-me-with-at-least-32-bytes"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://prolog:prolog@localhost:5432/prolog"
    sql_echo: bool = False

    # 기본값을 두지 않는다 — 운영 env 에서 빠지면 공개된 값으로 서명해 누구나 토큰을 위조한다.
    # 없거나 짧으면 기동이 실패한다. 생성 명령은 .env.example 에 있다.
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 60 * 24 * 5
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14
    # 2단계 가입 티켓. TTL 10분, 전용 aud (AUTH 계약 v0.2 §4.2).
    signup_ticket_ttl_seconds: int = 60 * 10

    google_client_id: str = ""
    google_client_secret: str = ""
    # ASSUMPTION: Google 토큰 엔드포인트가 code 교환 시 redirect_uri 를 요구한다. 명세에 값이 없다.
    google_redirect_uri: str = "http://localhost:3000/auth/callback"
    naver_client_id: str = ""
    naver_client_secret: str = ""
    # ASSUMPTION: 명세에 초대 만료가 없다. 7일로 둔다.
    invitation_ttl_seconds: int = 60 * 60 * 24 * 7

    # SQS 호환 큐. compose 는 elasticmq 다. AWS 로 옮길 때 endpoint 만 비우면 된다.
    sqs_endpoint_url: str = "http://localhost:9324"
    sqs_region: str = "elasticmq"
    # 로컬 .env 는 elasticmq/s3mock 용 가짜 키를 넣는다. 운영에서는 값을 비워 boto3 가
    # EC2 instance profile 같은 표준 credential provider chain 을 사용하게 한다.
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    outbox_relay_batch: int = 100
    outbox_relay_idle_seconds: float = 1.0
    # 잡 폴링 간격 힌트. meta.retry_after_ms 로 내보낸다 (ROADMAP Phase 1).
    job_retry_after_ms: int = 1000

    # --- 잡 내구성 (Phase 2a). 운영 중 환경변수로 조정한다 ---------------------------
    # 좀비 판정: running 인데 started_at 이 이만큼 지났으면 워커가 죽은 것으로 본다.
    # 장편 원고 추출이 정상적으로 이보다 오래 걸리면 살아 있는 잡을 죽인다 — 그때 늘린다.
    job_zombie_seconds_io: int = 5 * 60
    # ai 잡은 도는 동안 하트비트를 찍는다(0011) — 마지막 하트비트가 이만큼 끊기면 좀비다. 잡 전체
    # 길이와 무관하므로 짧게 둔다(워커가 죽은 잡을 빨리 줍는다). 하트비트 주기의 몇 배여야 한다.
    job_zombie_seconds_ai: int = 5 * 60
    job_heartbeat_seconds: int = 60
    # prolog-ai 는 .env 를 읽지 않는다. 여기 값을 첫 호출 때 환경변수로 넘긴다(app.ai.client).
    openrouter_api_key: str = ""
    # OpenRouter 키가 없을 때만 패키지가 OpenAI 를 직접 부른다(로컬 개발용). org·project 는 선택.
    openai_api_key: str = ""
    openai_org_id: str = ""
    openai_project_id: str = ""
    prolog_ai_model: str = ""
    # ai 워커가 동시에 돌리는 잡 수. 대부분 LLM 응답을 기다리는 시간이라 CPU 가 아니라
    # OpenRouter 속도 제한 · DB 커넥션(잡당 동시 1개)이 상한이다.
    ai_worker_concurrency: int = 4
    # 업로드 콜백 유실 스위퍼: URL 발급(= 원고 updated_at) 후 이만큼 지난 draft 를 본다.
    # presigned TTL(10분) + 여유 5분.
    upload_sweep_after_seconds: int = 15 * 60
    # 이만큼 받고도 지워지지 않은 메시지는 `{queue}-dlq` 로 간다 (SQS redrive).
    queue_max_receive_count: int = 3
    # 메시지를 받은 뒤 다른 소비자에게 다시 보이기까지. elasticmq 기본 30초는 AI 잡에 짧다 —
    # prolog-ai 는 호출당 30~90초 · 최대 3회 시도(NLCD 최악 약 93초, REX·AIQ·SCDS 약 273초,
    # SSM 은 챕터 수만큼). 시간 안에 못 끝내도 잡이 두 번 돌지는 않는다(선점이 막는다) —
    # 헛수신을 줄이는 값이다. 살아 있는 잡을 지키는 것은 하트비트다(0011).
    queue_visibility_seconds_ai: int = 15 * 60
    queue_visibility_seconds_io: int = 60
    sweeper_interval_seconds: float = 30.0
    # 원고 기억(0014): 챕터가 이만큼 조용하면 조각을 다시 색인한다(자동저장마다 임베딩하지 않게).
    memory_quiet_seconds: int = 180
    # AIQ 가 질문마다 넘기는 검색 조각 수. 패키지가 글자 예산으로 한 번 더 자른다.
    aiq_passage_count: int = 8

    # 원고 업로드는 presigned URL 이다(multipart 아님). 서버는 파일을 통과시키지 않는다.
    s3_bucket: str = "prolog-local"
    s3_endpoint_url: str = ""
    s3_region: str = "ap-northeast-2"
    presigned_ttl_seconds: int = 600

    @field_validator("jwt_secret")
    @classmethod
    def _reject_public_secret(cls, value: str) -> str:
        if value == _PUBLIC_JWT_SECRET:
            raise ValueError("JWT_SECRET 이 공개된 예시 값이다. 새로 생성해 넣는다")
        return value


settings = Settings()  # type: ignore[call-arg]  # jwt_secret 은 env 에서 채운다
