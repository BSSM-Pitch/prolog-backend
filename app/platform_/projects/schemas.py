from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
    # 명세 §2.1 의 식별자 필드명은 project_id 다. ORM 의 id 를 alias 로 읽는다.
    # populate_by_name: response_model 이 직렬화한 dict({"project_id": ...}) 를 다시 검증한다.
    model_config = ConfigDict(populate_by_name=True)

    project_id: UUID = Field(validation_alias="id")
    title: str
    description: str | None
    owner_type: OwnerType
    team_id: UUID | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    # content.manuscripts 는 Ring 2 라 platform_ 에서 셀 수 없다(규칙 1).
    # 조합 레이어(app/api/router.py)가 실제 집계로 채운다.
    manuscript_count: int = 0


class ProjectMemberResponse(BaseModel):
    # 표시용 username · 출처(source) 는 조합 레이어가 붙인다 (app/api/router.py).
    project_id: UUID
    user_id: UUID
    role: ProjectRole
    joined_at: datetime


class ProjectMemberUpdate(BaseModel):
    role: ProjectRole


class ProjectInvitationCreate(BaseModel):
    invited_email: EmailStr
    role: ProjectInviteRole = "editor"


class ProjectInvitationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    invitation_id: UUID = Field(validation_alias="id")
    project_id: UUID
    invited_email: str
    role: ProjectInviteRole
    status: str
    expires_at: datetime
    created_at: datetime


class ProjectInvitationCreatedResponse(ProjectInvitationResponse):
    """생성 응답에만 원문 토큰을 담는다. DB 에는 sha256 만 있다 (CLAUDE.md §6.4)."""

    token: str


class ProjectInvitationAccept(BaseModel):
    """수락 권한은 이메일 일치가 아니라 **토큰 소지**로 판정한다 (CLAUDE.md §6.4)."""

    token: str = Field(min_length=1)
