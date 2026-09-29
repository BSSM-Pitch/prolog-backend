from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://prolog:prolog@localhost:5432/prolog"
    sql_echo: bool = False

    jwt_secret: str = "dev-secret-change-me-with-at-least-32-bytes"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 30
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
    # elasticmq 는 서명을 검증하지 않지만 boto3 가 자격증명을 요구한다.
    aws_access_key_id: str = "local"
    aws_secret_access_key: str = "local"
    outbox_relay_batch: int = 100
    outbox_relay_idle_seconds: float = 1.0
    # 잡 폴링 간격 힌트. meta.retry_after_ms 로 내보낸다 (ROADMAP Phase 1).
    job_retry_after_ms: int = 1000

    # --- 잡 내구성 (Phase 2a). 운영 중 환경변수로 조정한다 ---------------------------
    # 좀비 판정: running 인데 started_at 이 이만큼 지났으면 워커가 죽은 것으로 본다.
    # 장편 원고 추출이 정상적으로 이보다 오래 걸리면 살아 있는 잡을 죽인다 — 그때 늘린다.
    job_zombie_seconds_io: int = 5 * 60
    job_zombie_seconds_ai: int = 15 * 60
    # 업로드 콜백 유실 스위퍼: URL 발급(= 원고 updated_at) 후 이만큼 지난 draft 를 본다.
    # presigned TTL(10분) + 여유 5분.
    upload_sweep_after_seconds: int = 15 * 60
    # 이만큼 받고도 지워지지 않은 메시지는 `{queue}-dlq` 로 간다 (SQS redrive).
    queue_max_receive_count: int = 3
    sweeper_interval_seconds: float = 30.0

    # 원고 업로드는 presigned URL 이다(multipart 아님). 서버는 파일을 통과시키지 않는다.
    s3_bucket: str = "prolog-local"
    s3_endpoint_url: str = ""
    s3_region: str = "ap-northeast-2"
    presigned_ttl_seconds: int = 600


settings = Settings()
