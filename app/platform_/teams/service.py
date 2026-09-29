from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from secrets import compare_digest
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import settings
from app.core.deps import TEAM_ROLE_RANK, CurrentUser
from app.core.security import new_opaque_token, sha256
from app.events.outbox import emit
from app.platform_.teams import repository as repo
from app.platform_.teams.models import (
    PROJECTS_TEAM_FK,
    TEAM_INVITATIONS_PENDING_UQ,
    Team,
    TeamInvitation,
    TeamMember,
)
from app.platform_.teams.schemas import (
    TeamCreate,
    TeamInvitationCreate,
    TeamInvitationCreatedResponse,
    TeamInvitationResponse,
    TeamMemberResponse,
    TeamResponse,
    TeamUpdate,
)


def _to_response(team: Team, member_count: int) -> TeamResponse:
    return TeamResponse(
        team_id=team.id,
        name=team.name,
        description=team.description,
        created_by=team.created_by,
        created_at=team.created_at,
        updated_at=team.updated_at,
        member_count=member_count,
    )


async def _team(session: AsyncSession, team: Team) -> TeamResponse:
    counts = await repo.member_counts(session, [team.id])
    return _to_response(team, counts.get(team.id, 0))


async def to_responses(session: AsyncSession, teams: Sequence[Team]) -> list[TeamResponse]:
    """목록용. 팀 수만큼 집계 쿼리를 날리지 않는다."""
    counts = await repo.member_counts(session, [t.id for t in teams])
    return [_to_response(t, counts.get(t.id, 0)) for t in teams]


def _invitation(row: TeamInvitation) -> TeamInvitationResponse:
    return TeamInvitationResponse.model_validate(row, from_attributes=True)


def _member(row: TeamMember) -> TeamMemberResponse:
    return TeamMemberResponse.model_validate(row, from_attributes=True)


async def create_team(session: AsyncSession, user: CurrentUser, body: TeamCreate) -> TeamResponse:
    team = Team(name=body.name, description=body.description, created_by=user.id)
    session.add(team)
    await session.flush()
    await repo.add_member(session, team.id, user.id, "owner")
    return await _team(session, team)


async def list_teams(
    session: AsyncSession, user: CurrentUser, limit: int, cursor: tuple[datetime, UUID] | None
) -> list[Team]:
    return await repo.list_teams_of_user(session, user.id, limit, cursor)


async def get_team(session: AsyncSession, team_id: UUID) -> TeamResponse:
    team = await repo.get_team(session, team_id)
    if team is None:
        raise errors.TeamNotFound()
    return await _team(session, team)


async def update_team(session: AsyncSession, team_id: UUID, body: TeamUpdate) -> TeamResponse:
    team = await repo.get_team(session, team_id)
    if team is None:
        raise errors.TeamNotFound()
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(team, field, value)
    await session.flush()
    return await _team(session, team)


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
        if errors.constraint_name(exc) == PROJECTS_TEAM_FK:
            raise errors.TeamHasActiveProjects() from exc
        raise


async def list_members(
    session: AsyncSession,
    team_id: UUID,
    limit: int | None = None,
    cursor: tuple[datetime, UUID] | None = None,
) -> list[TeamMemberResponse]:
    """`limit` 이 없으면 전원이다 — 팀 프로젝트 멤버 목록이 팀원 전체를 합칠 때 쓴다."""
    return [_member(m) for m in await repo.list_members(session, team_id, limit, cursor)]


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
    return _member(member)


async def remove_member(
    session: AsyncSession, team_id: UUID, user_id: UUID, actor: CurrentUser, actor_role: str
) -> None:
    # 본인 탈퇴 또는 owner|admin 의 팀원 제거 (TEAM 명세 §4.12).
    if user_id != actor.id and TEAM_ROLE_RANK[actor_role] < TEAM_ROLE_RANK["admin"]:
        raise errors.Forbidden()
    member = await repo.get_member(session, team_id, user_id)
    if member is None:
        raise errors.TeamMemberNotFound()
    if member.role == "owner" and await repo.count_owners(session, team_id) == 1:
        raise errors.LastOwnerCannotLeave()
    await session.delete(member)
    await session.flush()


async def invite(
    session: AsyncSession, team_id: UUID, user: CurrentUser, body: TeamInvitationCreate
) -> TeamInvitationCreatedResponse:
    token, token_hash = new_opaque_token()
    invitation = TeamInvitation(
        team_id=team_id,
        invited_email=str(body.invited_email),
        token_hash=token_hash,
        invited_by=user.id,
        role=body.role,
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.invitation_ttl_seconds),
    )
    session.add(invitation)
    try:
        await session.flush()
    except IntegrityError as exc:
        if errors.constraint_name(exc) == TEAM_INVITATIONS_PENDING_UQ:
            raise errors.DuplicateInvitation() from exc
        raise
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
    return TeamInvitationCreatedResponse(**_invitation(invitation).model_dump(), token=token)


async def list_invitations(session: AsyncSession, team_id: UUID) -> list[TeamInvitationResponse]:
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
    await session.flush()


async def _claim(
    session: AsyncSession, team_id: UUID, invitation_id: UUID, token: str
) -> TeamInvitation:
    invitation = await repo.get_invitation(session, invitation_id)
    if invitation is None or invitation.team_id != team_id:
        raise errors.TeamInvitationNotFound()
    # 토큰 소지가 곧 권한이다. 초대받은 주소와 로그인 주소가 달라도 된다 (CLAUDE.md §6.4).
    # **토큰을 상태보다 먼저 본다.** 순서가 반대면 토큰 없이도 "이미 처리됨"(409) 과
    # "없음"(404) 을 구분해 초대의 상태를 알아낼 수 있다. 틀린 토큰은 상태와 무관하게 404 다.
    if not compare_digest(sha256(token), invitation.token_hash):
        raise errors.TeamInvitationNotFound()
    if invitation.status != "pending":
        raise errors.InvitationNotPending()
    if invitation.expires_at <= datetime.now(UTC):
        raise errors.InvitationExpired()
    return invitation


async def accept_invitation(
    session: AsyncSession, team_id: UUID, invitation_id: UUID, user: CurrentUser, token: str
) -> TeamMemberResponse:
    """명세 §4.9: 응답은 생성된 TeamMember 다."""
    invitation = await _claim(session, team_id, invitation_id, token)
    if await repo.get_member(session, team_id, user.id) is not None:
        raise errors.AlreadyTeamMember()
    invitation.status = "accepted"
    invitation.accepted_at = func.now()
    member = await repo.add_member(session, team_id, user.id, invitation.role)
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
    await session.refresh(member)
    return _member(member)


async def is_member(session: AsyncSession, team_id: UUID, user_id: UUID) -> bool:
    return await repo.get_member(session, team_id, user_id) is not None
