from datetime import datetime
from uuid import UUID

from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform_.projects.models import Project, ProjectInvitation, ProjectMember


async def get_project(session: AsyncSession, project_id: UUID) -> Project | None:
    return await session.get(Project, project_id)


async def list_projects_of_user(
    session: AsyncSession, user_id: UUID, limit: int, cursor: tuple[datetime, UUID] | None
) -> list[Project]:
    stmt = (
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user_id)
        .order_by(Project.created_at.desc(), Project.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Project.created_at, Project.id) < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def list_members(session: AsyncSession, project_id: UUID) -> list[ProjectMember]:
    stmt = (
        select(ProjectMember)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.joined_at.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def get_member(
    session: AsyncSession, project_id: UUID, user_id: UUID
) -> ProjectMember | None:
    return await session.get(ProjectMember, {"project_id": project_id, "user_id": user_id})


async def count_owners(session: AsyncSession, project_id: UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(ProjectMember)
        .where(ProjectMember.project_id == project_id, ProjectMember.role == "owner")
    )
    return (await session.execute(stmt)).scalar_one()


async def add_member(
    session: AsyncSession, project_id: UUID, user_id: UUID, role: str
) -> ProjectMember:
    member = ProjectMember(project_id=project_id, user_id=user_id, role=role, joined_at=func.now())
    session.add(member)
    await session.flush()
    return member


async def get_invitation(session: AsyncSession, invitation_id: UUID) -> ProjectInvitation | None:
    return await session.get(ProjectInvitation, invitation_id)


async def list_invitations(session: AsyncSession, project_id: UUID) -> list[ProjectInvitation]:
    stmt = (
        select(ProjectInvitation)
        .where(ProjectInvitation.project_id == project_id)
        .order_by(ProjectInvitation.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())
