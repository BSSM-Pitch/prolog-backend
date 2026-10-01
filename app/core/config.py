from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://prolog:prolog@localhost:5432/prolog"
    sql_echo: bool = False

    jwt_secret: str = "dev-secret-change-me-with-at-least-32-bytes"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 60 * 24 * 5
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14
    # 2단계 가입 티켓. TTL 10분, 전용 aud (AUTH 계약 v0.2 §4.2).
    signup_ticket_ttl_seconds: int = 60 * 10

    google_client_id: str = ""
    google_client_secret: str = ""
    # ASSUMPTION: Google 토큰 엔드포인트가 code 교환 시 redirect_uri 를 요구한다. 명세에 값이 없다.
    google_redirect_uri: str = "http://localhost:3000/auth/callback"
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
    prolog_ai_model: str = ""
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

    # 원고 업로드는 presigned URL 이다(multipart 아님). 서버는 파일을 통과시키지 않는다.
    s3_bucket: str = "prolog-local"
    s3_endpoint_url: str = ""
    s3_region: str = "ap-northeast-2"
    presigned_ttl_seconds: int = 600


settings = Settings()
