from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform_.teams.models import Team, TeamInvitation, TeamMember


async def get_team(session: AsyncSession, team_id: UUID) -> Team | None:
    return await session.get(Team, team_id)


async def list_teams_of_user(
    session: AsyncSession, user_id: UUID, limit: int, cursor: tuple[datetime, UUID] | None
) -> list[Team]:
    stmt = (
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id)
        .order_by(Team.created_at.desc(), Team.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Team.created_at, Team.id) < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def list_members(session: AsyncSession, team_id: UUID) -> list[TeamMember]:
    stmt = (
        select(TeamMember).where(TeamMember.team_id == team_id).order_by(TeamMember.joined_at.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def get_member(session: AsyncSession, team_id: UUID, user_id: UUID) -> TeamMember | None:
    return await session.get(TeamMember, {"team_id": team_id, "user_id": user_id})


async def member_counts(session: AsyncSession, team_ids: Sequence[UUID]) -> dict[UUID, int]:
    """teams.member_count 는 저장하지 않는다. 조회 시 집계한다 (CLAUDE.md §7)."""
    if not team_ids:
        return {}
    stmt = (
        select(TeamMember.team_id, func.count())
        .where(TeamMember.team_id.in_(team_ids))
        .group_by(TeamMember.team_id)
    )
    return {row[0]: row[1] for row in (await session.execute(stmt))}


async def count_owners(session: AsyncSession, team_id: UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(TeamMember)
        .where(TeamMember.team_id == team_id, TeamMember.role == "owner")
    )
    return (await session.execute(stmt)).scalar_one()


async def add_member(session: AsyncSession, team_id: UUID, user_id: UUID, role: str) -> TeamMember:
    member = TeamMember(team_id=team_id, user_id=user_id, role=role, joined_at=func.now())
    session.add(member)
    await session.flush()
    return member


async def get_invitation(session: AsyncSession, invitation_id: UUID) -> TeamInvitation | None:
    return await session.get(TeamInvitation, invitation_id)


async def list_invitations(session: AsyncSession, team_id: UUID) -> list[TeamInvitation]:
    stmt = (
        select(TeamInvitation)
        .where(TeamInvitation.team_id == team_id)
        .order_by(TeamInvitation.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())
