from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://prolog:prolog@localhost:5432/prolog"
    sql_echo: bool = False

    jwt_secret: str = "dev-secret-change-me-with-at-least-32-bytes"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 30
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14
    # ASSUMPTION: 명세에 초대 만료가 없다. 7일로 둔다.
    invitation_ttl_seconds: int = 60 * 60 * 24 * 7


settings = Settings()
