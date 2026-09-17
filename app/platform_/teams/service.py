from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import settings
from app.core.deps import CurrentUser
from app.events.outbox import emit
from app.platform_.teams import repository as repo
from app.platform_.teams.models import Team, TeamInvitation
from app.platform_.teams.schemas import (
    InvitationCreate,
    InvitationResponse,
    TeamCreate,
    TeamMemberResponse,
    TeamResponse,
    TeamUpdate,
)


def _team(team: Team) -> TeamResponse:
    return TeamResponse.model_validate(team, from_attributes=True)


def _invitation(row: TeamInvitation) -> InvitationResponse:
    return InvitationResponse.model_validate(row, from_attributes=True)


async def create_team(session: AsyncSession, user: CurrentUser, body: TeamCreate) -> TeamResponse:
    team = Team(name=body.name, description=body.description, created_by=user.id)
    session.add(team)
    await session.flush()
    await repo.add_member(session, team.id, user.id, "owner")
    return _team(team)


async def list_teams(
    session: AsyncSession, user: CurrentUser, limit: int, cursor: tuple[datetime, UUID] | None
) -> list[Team]:
    return await repo.list_teams_of_user(session, user.id, limit, cursor)


async def get_team(session: AsyncSession, team_id: UUID) -> TeamResponse:
    team = await repo.get_team(session, team_id)
    if team is None:
        raise errors.TeamNotFound()
    return _team(team)


async def update_team(session: AsyncSession, team_id: UUID, body: TeamUpdate) -> TeamResponse:
    team = await repo.get_team(session, team_id)
    if team is None:
        raise errors.TeamNotFound()
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(team, field, value)
    await session.flush()
    return _team(team)


async def delete_team(session: AsyncSession, team_id: UUID) -> None:
    team = await repo.get_team(session, team_id)
    if team is None:
        raise errors.TeamNotFound()
    await session.delete(team)
    try:
        await session.flush()
    except IntegrityError as exc:
        # projects.team_id 가 ON DELETE RESTRICT 다. 사전 카운트는 PRJ 테이블을 읽어야 해서
        # 같은 Ring 동기 호출이 되므로(규칙 3), 제약 위반을 그대로 409 로 번역한다.
        raise errors.TeamHasActiveProjects() from exc


async def list_members(session: AsyncSession, team_id: UUID) -> list[TeamMemberResponse]:
    return [
        TeamMemberResponse.model_validate(m, from_attributes=True)
        for m in await repo.list_members(session, team_id)
    ]


async def update_member_role(
    session: AsyncSession, team_id: UUID, user_id: UUID, role: str
) -> TeamMemberResponse:
    member = await repo.get_member(session, team_id, user_id)
    if member is None:
        raise errors.TeamMemberNotFound()
    if (
        member.role == "owner"
        and role != "owner"
        and await repo.count_owners(session, team_id) == 1
    ):
        raise errors.LastOwnerCannotLeave()
    member.role = role
    await session.flush()
    return TeamMemberResponse.model_validate(member, from_attributes=True)


async def remove_member(session: AsyncSession, team_id: UUID, user_id: UUID) -> None:
    member = await repo.get_member(session, team_id, user_id)
    if member is None:
        raise errors.TeamMemberNotFound()
    if member.role == "owner" and await repo.count_owners(session, team_id) == 1:
        raise errors.LastOwnerCannotLeave()
    await session.delete(member)
    await session.flush()


async def invite(
    session: AsyncSession, team_id: UUID, user: CurrentUser, body: InvitationCreate
) -> InvitationResponse:
    invitation = TeamInvitation(
        team_id=team_id,
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
    # 알림 생성 API 는 없다. 도메인 변경과 같은 트랜잭션에서 outbox 로 넘긴다(§8).
    emit(
        session,
        aggregate_type="team_invitation",
        aggregate_id=invitation.id,
        event_type="team.invited",
        payload={
            "team_id": str(team_id),
            "invitation_id": str(invitation.id),
            "invited_email": invitation.invited_email,
            "invited_by": str(user.id),
            "role": invitation.role,
            "expires_at": invitation.expires_at.isoformat(),
        },
    )
    return _invitation(invitation)


async def list_invitations(session: AsyncSession, team_id: UUID) -> list[InvitationResponse]:
    return [_invitation(i) for i in await repo.list_invitations(session, team_id)]


async def _pending(session: AsyncSession, team_id: UUID, invitation_id: UUID) -> TeamInvitation:
    invitation = await repo.get_invitation(session, invitation_id)
    if invitation is None or invitation.team_id != team_id:
        raise errors.TeamInvitationNotFound()
    if invitation.status != "pending":
        raise errors.InvitationNotPending()
    return invitation


async def revoke_invitation(session: AsyncSession, team_id: UUID, invitation_id: UUID) -> None:
    invitation = await _pending(session, team_id, invitation_id)
    invitation.status = "revoked"
    invitation.responded_at = func.now()
    await session.flush()


async def respond_invitation(
    session: AsyncSession,
    team_id: UUID,
    invitation_id: UUID,
    user: CurrentUser,
    accept: bool,
) -> InvitationResponse:
    invitation = await _pending(session, team_id, invitation_id)
    if invitation.expires_at <= datetime.now(UTC):
        raise errors.InvitationExpired()
    if not user.email or user.email.lower() != invitation.invited_email.lower():
        raise errors.InvitationEmailMismatch()

    invitation.status = "accepted" if accept else "rejected"
    invitation.responded_at = func.now()
    if accept:
        if await repo.get_member(session, team_id, user.id) is not None:
            raise errors.AlreadyTeamMember()
        await repo.add_member(session, team_id, user.id, invitation.role)
        emit(
            session,
            aggregate_type="team",
            aggregate_id=team_id,
            event_type="team.member_joined",
            payload={
                "team_id": str(team_id),
                "user_id": str(user.id),
                "role": invitation.role,
                "invitation_id": str(invitation.id),
            },
        )
    await session.flush()
    await session.refresh(invitation)
    return _invitation(invitation)
