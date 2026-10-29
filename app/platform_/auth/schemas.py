from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.core.response import Envelope

UserRole = Literal["writer", "aspiring_writer", "reader"]
USERNAME_MAX = 30


class OAuthGoogleRequest(BaseModel):
    oauth_code: str = Field(min_length=1)


class OAuthNaverRequest(BaseModel):
    oauth_code: str = Field(min_length=1)
    # 네이버 인가 요청 때 보낸 state. 토큰 교환에 그대로 넘긴다.
    state: str | None = None


class SignupRequest(BaseModel):
    """소셜 가입은 `signup_ticket`, 일반 가입은 `email` + `password`. 둘 중 하나만 보낸다."""

    signup_ticket: str | None = Field(default=None, min_length=1)
    email: EmailStr | None = None
    # 규칙(영문·숫자 포함 8자 이상)은 서비스에서 INVALID_INPUT 으로 답한다. 상한은 해시 비용 제한.
    password: str | None = Field(default=None, max_length=128)
    # 누락은 USERNAME_REQUIRED(400) 로 답해야 해서 스키마에서 막지 않는다 (AUTH 명세 §1.4).
    username: str | None = Field(default=None, max_length=USERNAME_MAX)
    role: UserRole


class LoginRequest(BaseModel):
    # 아이디 또는 이메일 (`@` 가 있으면 이메일로 찾는다).
    login_id: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class UserUpdate(BaseModel):
    # v0.2 에서 수정 가능한 필드는 role 뿐이다 (CLAUDE.md §4.1).
    role: UserRole


class UserResponse(BaseModel):
    user_id: UUID
    username: str
    email: str | None
    role: UserRole
    auth_provider: str
    created_at: datetime
    updated_at: datetime
    # users.plan 과 provider_user_id 는 어떤 응답에도 노출하지 않는다.


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class SessionResponse(BaseModel):
    user: UserResponse
    tokens: TokenResponse


class SignupTicketResponse(BaseModel):
    """신규 사용자. 계정은 아직 만들지 않았다 (AUTH 계약 §4.2)."""

    signup_ticket: str
    email: str | None


class UsernameCheckResponse(BaseModel):
    username: str
    available: bool


class OAuthMeta(BaseModel):
    is_new_user: bool = Field(
        description="true 면 data 는 가입 티켓이다 — `/auth/signup` 으로 가입을 마친다"
    )


class OAuthGoogleResponse(Envelope[SessionResponse | SignupTicketResponse]):
    """기존 사용자는 세션, 신규 사용자는 가입 티켓. `meta.is_new_user` 로 가른다."""

    meta: OAuthMeta  # type: ignore[assignment]


class OAuthNaverResponse(OAuthGoogleResponse):
    """네이버도 구글과 같다 — 기존 사용자는 세션, 신규 사용자는 가입 티켓."""
