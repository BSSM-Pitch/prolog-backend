from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

# ASSUMPTION: AUTH 명세 원문을 참조하지 못했다. 필드명/경로는 아래로 고정하고 명세 확인 시 조정한다.
UserRole = Literal["writer", "aspiring_writer", "reader"]


class SignupRequest(BaseModel):
    email: EmailStr
    # bcrypt 는 72바이트를 넘는 입력을 잘라내므로 상한을 명시한다.
    password: str = Field(min_length=8, max_length=72)
    nickname: str = Field(min_length=1, max_length=50)
    role: UserRole = "writer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: UUID
    email: str | None
    nickname: str
    role: UserRole
    created_at: datetime
    # users.plan 은 어떤 응답 스키마에도 노출하지 않는다 (CLAUDE.md §5).


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class SessionResponse(BaseModel):
    user: UserResponse
    token: TokenResponse
