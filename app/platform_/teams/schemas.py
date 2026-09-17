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
    id: UUID
    name: str
    description: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class TeamMemberResponse(BaseModel):
    # ASSUMPTION: 표시용 nickname/email 은 넣지 않는다. platform.users 는 AUTH 소유이고
    # 같은 Ring 안의 동기 호출이 금지되어 있다(규칙 3). 표시 데이터는 별도 결정이 필요하다.
    user_id: UUID
    role: TeamRole
    joined_at: datetime


class TeamMemberUpdate(BaseModel):
    role: TeamRole


class InvitationCreate(BaseModel):
    email: EmailStr
    role: TeamRole = "member"


class InvitationResponse(BaseModel):
    id: UUID
    team_id: UUID
    invited_email: str
    role: TeamRole
    status: str
    expires_at: datetime
    created_at: datetime
