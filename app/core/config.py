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


settings = Settings()
