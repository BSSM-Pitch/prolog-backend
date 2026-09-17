from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

ProjectRole = Literal["owner", "editor", "viewer"]
OwnerType = Literal["personal", "team"]


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str | None = None
    owner_type: OwnerType = "personal"
    team_id: UUID | None = None


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None


class ProjectResponse(BaseModel):
    # 명세 §2.1 의 식별자 필드명은 project_id 다.
    project_id: UUID = Field(validation_alias="id")
    title: str
    description: str | None
    owner_type: OwnerType
    team_id: UUID | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    # manuscript_count 는 명세 §2.1 에 있으나 아직 없다 (감사 A표 P2).


class ProjectMemberResponse(BaseModel):
    # ASSUMPTION: TeamMemberResponse 와 같은 이유로 표시용 username 을 아직 넣지 않는다.
    project_id: UUID
    user_id: UUID
    role: ProjectRole
    joined_at: datetime


class ProjectMemberUpdate(BaseModel):
    role: ProjectRole


class InvitationCreate(BaseModel):
    invited_email: EmailStr
    role: ProjectRole = "editor"


class InvitationResponse(BaseModel):
    invitation_id: UUID = Field(validation_alias="id")
    project_id: UUID
    invited_email: str
    role: ProjectRole
    status: str
    expires_at: datetime
    created_at: datetime
