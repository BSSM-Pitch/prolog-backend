from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

ProjectRole = Literal["owner", "editor", "viewer"]
OwnerType = Literal["personal", "team"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    owner_type: OwnerType = "personal"
    team_id: UUID | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    owner_type: OwnerType
    team_id: UUID | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ProjectMemberResponse(BaseModel):
    # ASSUMPTION: TeamMemberResponse 와 같은 이유로 표시용 사용자 정보를 넣지 않는다.
    user_id: UUID
    role: ProjectRole
    joined_at: datetime


class ProjectMemberUpdate(BaseModel):
    role: ProjectRole


class InvitationCreate(BaseModel):
    email: EmailStr
    role: ProjectRole = "editor"


class InvitationResponse(BaseModel):
    id: UUID
    project_id: UUID
    invited_email: str
    role: ProjectRole
    status: str
    expires_at: datetime
    created_at: datetime
