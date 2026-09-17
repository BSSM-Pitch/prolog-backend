from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

UserRole = Literal["writer", "aspiring_writer", "reader"]
USERNAME_MAX = 30


class OAuthGoogleRequest(BaseModel):
    oauth_code: str = Field(min_length=1)


class SignupRequest(BaseModel):
    signup_ticket: str = Field(min_length=1)
    # 누락은 USERNAME_REQUIRED(400) 로 답해야 해서 스키마에서 막지 않는다 (AUTH 명세 §1.4).
    username: str | None = Field(default=None, max_length=USERNAME_MAX)
    role: UserRole


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
