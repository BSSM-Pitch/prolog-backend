from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

TeamRole = Literal["owner", "admin", "member"]
# 초대로는 owner 를 줄 수 없다 (명세 §2.3). DDL CHECK 도 같다.
TeamInviteRole = Literal["admin", "member"]


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None


class TeamResponse(BaseModel):
    # 명세 §2.1 의 식별자 필드명은 team_id 다. ORM 의 id 를 alias 로 읽는다.
    model_config = ConfigDict(populate_by_name=True)

    team_id: UUID = Field(validation_alias="id")
    name: str
    description: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    # 저장하지 않는다. 조회 시 집계한다 — 동시 가입 경합으로 카운터가 틀어진다(CLAUDE.md §7).
    member_count: int


class TeamMemberResponse(BaseModel):
    # ASSUMPTION: 표시용 username 은 아직 넣지 않는다. platform.users 는 AUTH 소유이고
    # 같은 Ring 안의 동기 호출이 금지되어 있다(규칙 3). §6.3 결정 대기.
    team_id: UUID
    user_id: UUID
    role: TeamRole
    joined_at: datetime


class TeamMemberUpdate(BaseModel):
    role: TeamRole


class InvitationCreate(BaseModel):
    invited_email: EmailStr
    role: TeamInviteRole = "member"


class InvitationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    invitation_id: UUID = Field(validation_alias="id")
    team_id: UUID
    invited_email: str
    role: TeamInviteRole
    status: str
    expires_at: datetime
    created_at: datetime


class InvitationCreatedResponse(InvitationResponse):
    """생성 응답에만 원문 토큰을 담는다. DB 에는 sha256 만 있다 (CLAUDE.md §6.4)."""

    token: str


class InvitationAccept(BaseModel):
    """수락 권한은 이메일 일치가 아니라 **토큰 소지**로 판정한다 (CLAUDE.md §6.4)."""

    token: str = Field(min_length=1)
