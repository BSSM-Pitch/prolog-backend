from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import settings
from app.core.deps import CurrentUser, team_role_of
from app.events.outbox import emit
from app.platform_.projects import repository as repo
from app.platform_.projects.models import Project, ProjectInvitation
from app.platform_.projects.schemas import (
    InvitationCreate,
    InvitationResponse,
    ProjectCreate,
    ProjectMemberResponse,
    ProjectResponse,
    ProjectUpdate,
)


def _project(project: Project) -> ProjectResponse:
    return ProjectResponse.model_validate(project, from_attributes=True)


def _invitation(row: ProjectInvitation) -> InvitationResponse:
    return InvitationResponse.model_validate(row, from_attributes=True)


async def create_project(
    session: AsyncSession, user: CurrentUser, body: ProjectCreate
) -> ProjectResponse:
    if (body.owner_type == "team") != (body.team_id is not None):
        raise errors.InvalidOwnerType()
    if body.team_id is not None:
        # 요구되는 것은 팀 소속 여부뿐이다. 팀원 누구나 만든다 (CLAUDE.md §6.2).
        # 팀 멤버십 확인은 core.deps 의 raw SQL 이 담당한다(TEAM 모듈 동기 호출 금지, 규칙 3).
        # 팀이 없으면 team_role_of 가 TEAM_NOT_FOUND(404) 를 던진다 (PRJ 명세 §1.4).
        if await team_role_of(session, body.team_id, user.id) is None:
            raise errors.NotTeamMember()

    project = Project(
        name=body.name,
        description=body.description,
        owner_type=body.owner_type,
        team_id=body.team_id,
        created_by=user.id,
    )
    session.add(project)
    await session.flush()
    # 팀 프로젝트라도 project_members 에는 생성자만 넣는다. 나머지 팀원의 권한은
    # project_role_of 가 team_members 를 함께 읽어 해석한다 (core/deps.py).
    await repo.add_member(session, project.id, user.id, "owner")
    return _project(project)


async def list_projects(
    session: AsyncSession, user: CurrentUser, limit: int, cursor: tuple[datetime, UUID] | None
) -> list[Project]:
    return await repo.list_projects_of_user(session, user.id, limit, cursor)


async def get_project(session: AsyncSession, project_id: UUID) -> ProjectResponse:
    project = await repo.get_project(session, project_id)
    if project is None:
        raise errors.ProjectNotFound()
    return _project(project)


async def update_project(
    session: AsyncSession, project_id: UUID, body: ProjectUpdate
) -> ProjectResponse:
    project = await repo.get_project(session, project_id)
    if project is None:
        raise errors.ProjectNotFound()
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    await session.flush()
    return _project(project)


async def delete_project(session: AsyncSession, project_id: UUID) -> None:
    project = await repo.get_project(session, project_id)
    if project is None:
        raise errors.ProjectNotFound()
    await session.delete(project)
    await session.flush()


async def list_members(session: AsyncSession, project_id: UUID) -> list[ProjectMemberResponse]:
    return [
        ProjectMemberResponse.model_validate(m, from_attributes=True)
        for m in await repo.list_members(session, project_id)
    ]


async def update_member_role(
    session: AsyncSession, project_id: UUID, user_id: UUID, role: str
) -> ProjectMemberResponse:
    member = await repo.get_member(session, project_id, user_id)
    if member is None:
        raise errors.MemberNotFound()
    if (
        member.role == "owner"
        and role != "owner"
        and await repo.count_owners(session, project_id) == 1
    ):
        raise errors.LastOwnerCannotLeave()
    member.role = role
    await session.flush()
    return ProjectMemberResponse.model_validate(member, from_attributes=True)


async def remove_member(session: AsyncSession, project_id: UUID, user_id: UUID) -> None:
    member = await repo.get_member(session, project_id, user_id)
    if member is None:
        raise errors.MemberNotFound()
    if member.role == "owner" and await repo.count_owners(session, project_id) == 1:
        raise errors.LastOwnerCannotLeave()
    await session.delete(member)
    await session.flush()


async def invite(
    session: AsyncSession, project_id: UUID, user: CurrentUser, body: InvitationCreate
) -> InvitationResponse:
    invitation = ProjectInvitation(
        project_id=project_id,
        invited_email=str(body.email),
        invited_by=user.id,
        role=body.role,
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.invitation_ttl_seconds),
    )
    session.add(invitation)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise errors.DuplicateInvitation() from exc
    emit(
        session,
        aggregate_type="project_invitation",
        aggregate_id=invitation.id,
        event_type="project.invited",
        payload={
            "project_id": str(project_id),
            "invitation_id": str(invitation.id),
            "invited_email": invitation.invited_email,
            "invited_by": str(user.id),
            "role": invitation.role,
            "expires_at": invitation.expires_at.isoformat(),
        },
    )
    return _invitation(invitation)


async def list_invitations(session: AsyncSession, project_id: UUID) -> list[InvitationResponse]:
    return [_invitation(i) for i in await repo.list_invitations(session, project_id)]


async def _pending(
    session: AsyncSession, project_id: UUID, invitation_id: UUID
) -> ProjectInvitation:
    invitation = await repo.get_invitation(session, invitation_id)
    if invitation is None or invitation.project_id != project_id:
        raise errors.InvitationNotFound()
    if invitation.status != "pending":
        raise errors.InvitationNotPending()
    return invitation


async def revoke_invitation(session: AsyncSession, project_id: UUID, invitation_id: UUID) -> None:
    invitation = await _pending(session, project_id, invitation_id)
    invitation.status = "revoked"
    invitation.responded_at = func.now()
    await session.flush()


async def respond_invitation(
    session: AsyncSession,
    project_id: UUID,
    invitation_id: UUID,
    user: CurrentUser,
    accept: bool,
) -> InvitationResponse:
    invitation = await _pending(session, project_id, invitation_id)
    if invitation.expires_at <= datetime.now(UTC):
        raise errors.InvitationExpired()
    if not user.email or user.email.lower() != invitation.invited_email.lower():
        raise errors.InvitationEmailMismatch()

    invitation.status = "accepted" if accept else "rejected"
    invitation.responded_at = func.now()
    if accept:
        if await repo.get_member(session, project_id, user.id) is not None:
            raise errors.AlreadyMember()
        await repo.add_member(session, project_id, user.id, invitation.role)
    await session.flush()
    await session.refresh(invitation)
    return _invitation(invitation)
