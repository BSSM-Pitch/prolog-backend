from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

ProjectRole = Literal["owner", "editor", "viewer"]
# 초대로는 owner 를 줄 수 없다 (명세 §2.3). DDL CHECK 도 같다.
ProjectInviteRole = Literal["editor", "viewer"]
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
    # ponytail: content.manuscripts 는 Ring 2 라 platform_ 에서 셀 수 없다(규칙 1).
    # Phase 0 에는 원고 쓰기 경로 자체가 없어 실제로 0 이다. MSU(Phase 1) 가 들어오면
    # 조합 레이어에서 실제 집계로 채운다 — 그 전까지는 상수다.
    manuscript_count: int = 0


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
    role: ProjectInviteRole = "editor"


class InvitationResponse(BaseModel):
    invitation_id: UUID = Field(validation_alias="id")
    project_id: UUID
    invited_email: str
    role: ProjectInviteRole
    status: str
    expires_at: datetime
    created_at: datetime
