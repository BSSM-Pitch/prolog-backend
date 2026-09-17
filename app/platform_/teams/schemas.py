from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

TeamRole = Literal["owner", "admin", "member"]


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None


class TeamResponse(BaseModel):
    # 명세 §2.1 의 식별자 필드명은 team_id 다. ORM 의 id 를 alias 로 읽는다.
    team_id: UUID = Field(validation_alias="id")
    name: str
    description: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    # member_count 는 명세 §2.1 에 있으나 아직 없다 (감사 A표 P2).


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
    role: TeamRole = "member"


class InvitationResponse(BaseModel):
    invitation_id: UUID = Field(validation_alias="id")
    team_id: UUID
    invited_email: str
    role: TeamRole
    status: str
    expires_at: datetime
    created_at: datetime
