"""인증 · 인가 의존성.

``require_project_role`` / ``require_team_role`` 은 platform 테이블을 **raw SQL** 로 읽는다.
core 가 ``platform_.projects.repository`` 를 import 하면 의존 방향이 뒤집히기 때문이다(규칙 2·4).
이 두 함수가 멤버십 테이블을 읽는 유일한 예외 지점이다.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.security import decode_access_token
from app.db.session import Session

_bearer = HTTPBearer(auto_error=False)

ProjectRole = str  # owner | editor | viewer  (project_members.role)
TeamRole = str  # owner | admin | member     (team_members.role)

PROJECT_ROLE_RANK: dict[str, int] = {"viewer": 0, "editor": 1, "owner": 2}
TEAM_ROLE_RANK: dict[str, int] = {"member": 0, "admin": 1, "owner": 2}

# ASSUMPTION: 팀 프로젝트에서 팀원이 갖는 프로젝트 role 을 명세가 정하지 않는다.
# PRJ §4.2·TEAM §5 가 "함께 작업"을 요구하므로 viewer 로는 부족하고, owner 를 주면
# 삭제(원고 cascade)·멤버 관리까지 팀원 전원에게 열린다. 조회+수정까지인 editor 로 둔다.
TEAM_MEMBER_PROJECT_ROLE = "editor"


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str | None


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None:
        raise errors.Unauthorized()
    payload = decode_access_token(credentials.credentials)
    # ponytail: 액세스 토큰만 검증하고 DB 를 치지 않는다. 즉시 차단이 필요해지면
    # refresh_tokens 폐기 + TTL 단축으로 먼저 해결하고, 그래도 부족하면 여기에 캐시된 조회를 넣는다.
    return CurrentUser(id=UUID(payload["sub"]), email=payload.get("email"))


User = Annotated[CurrentUser, Depends(current_user)]


@dataclass(frozen=True)
class ProjectContext:
    project_id: UUID
    role: ProjectRole
    user: CurrentUser


async def project_role_of(session: AsyncSession, project_id: UUID, user_id: UUID) -> str | None:
    """(존재하지 않는 프로젝트) → PROJECT_NOT_FOUND, (권한 없음) → None.

    팀 프로젝트는 **팀 소속만으로도** 권한이 생긴다. 팀 멤버를 ``project_members`` 로
    승격하지 않기 때문에, 이 조회가 두 출처를 합치는 유일한 지점이다.
    명시적 ``project_members`` 행이 더 높으면 그쪽이 이긴다.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT m.role AS role,
                       (t.user_id IS NOT NULL) AS is_team_member
                  FROM platform.projects p
                  LEFT JOIN platform.project_members m
                         ON m.project_id = p.id AND m.user_id = :user_id
                  LEFT JOIN platform.team_members t
                         ON t.team_id = p.team_id AND t.user_id = :user_id
                 WHERE p.id = :project_id
                """
            ),
            {"project_id": project_id, "user_id": user_id},
        )
    ).first()
    if row is None:
        raise errors.ProjectNotFound()
    role: str | None = row.role
    if row.is_team_member and (
        role is None or PROJECT_ROLE_RANK[role] < PROJECT_ROLE_RANK[TEAM_MEMBER_PROJECT_ROLE]
    ):
        return TEAM_MEMBER_PROJECT_ROLE
    return role


def require_project_role(min_role: ProjectRole):  # type: ignore[no-untyped-def]
    """CLAUDE.md §3: 프로젝트 권한은 이 의존성 하나로 수렴한다."""
    threshold = PROJECT_ROLE_RANK[min_role]

    async def dependency(
        session: Session,
        user: User,
        project_id: Annotated[UUID, Path(alias="projectId")],
    ) -> ProjectContext:
        role = await project_role_of(session, project_id, user.id)
        # ASSUMPTION: 멤버가 아닌 사용자에게 403 을 준다(존재 은닉보다 명세 관례를 따름).
        if role is None or PROJECT_ROLE_RANK[role] < threshold:
            raise errors.Forbidden()
        return ProjectContext(project_id=project_id, role=role, user=user)

    return dependency


async def team_role_of(session: AsyncSession, team_id: UUID, user_id: UUID) -> str | None:
    row = (
        await session.execute(
            text(
                """
                SELECT m.role AS role
                  FROM platform.teams t
                  LEFT JOIN platform.team_members m
                         ON m.team_id = t.id AND m.user_id = :user_id
                 WHERE t.id = :team_id
                """
            ),
            {"team_id": team_id, "user_id": user_id},
        )
    ).first()
    if row is None:
        raise errors.TeamNotFound()
    return row.role


async def assert_team_role(
    session: AsyncSession, team_id: UUID, user_id: UUID, min_role: TeamRole
) -> str:
    role = await team_role_of(session, team_id, user_id)
    if role is None or TEAM_ROLE_RANK[role] < TEAM_ROLE_RANK[min_role]:
        raise errors.Forbidden()
    return role


async def team_ids_of(session: AsyncSession, user_id: UUID) -> list[UUID]:
    """사용자가 속한 팀 id.

    팀 프로젝트를 목록에 포함시키려면 ``projects`` 쪽에서 팀 소속을 알아야 한다.
    멤버십 조회는 이 파일로 수렴시킨다 — 모듈이 남의 테이블을 직접 읽기 시작하면
    §3의 예외가 두 번째로 늘어난다.
    """
    rows = await session.execute(
        text("SELECT team_id FROM platform.team_members WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    return [row.team_id for row in rows]


def require_team_role(min_role: TeamRole):  # type: ignore[no-untyped-def]
    async def dependency(
        session: Session,
        user: User,
        team_id: Annotated[UUID, Path(alias="teamId")],
    ) -> str:
        return await assert_team_role(session, team_id, user.id, min_role)

    return dependency
