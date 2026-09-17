"""조합 레이어 — 두 모듈 이상의 데이터가 필요한 엔드포인트만 여기 둔다.

모듈끼리는 서로를 import 하지 못한다(규칙 3). 이 레이어는 모든 Ring 위에 있으므로
AUTH·TEAM·PRJ 를 각각 호출해 응답을 합칠 수 있다. `.importlinter` 의 `rings` 계약에
`api` 를 최상위 레이어로 등재해 두었으니 반대 방향(모듈 → api)은 기계가 막는다.

여기로 올라온 이유가 없는 엔드포인트는 모듈 라우터에 그대로 둔다.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.core import errors
from app.core.deps import User, require_project_role, require_team_role
from app.core.response import ok
from app.db.session import Session
from app.platform_.auth import service as auth
from app.platform_.projects import service as projects
from app.platform_.projects.schemas import InvitationCreate as ProjectInvitationCreate
from app.platform_.projects.schemas import ProjectMemberResponse
from app.platform_.teams import service as teams
from app.platform_.teams.schemas import InvitationCreate as TeamInvitationCreate
from app.platform_.teams.schemas import TeamMemberResponse

router = APIRouter()

TeamId = Annotated[UUID, Path(alias="teamId")]
ProjectId = Annotated[UUID, Path(alias="projectId")]
TeamMember = Depends(require_team_role("member"))
TeamAdmin = Depends(require_team_role("admin"))
ProjectViewer = Depends(require_project_role("viewer"))
ProjectOwner = Depends(require_project_role("owner"))


class TeamMemberWithUsername(TeamMemberResponse):
    """TEAM 명세 §4.6 의 "(사용자 이름 포함)". 필요한 것은 username 하나다(§6.3)."""

    username: str


class ProjectMemberWithUsername(ProjectMemberResponse):
    """PRJ 명세 §4.6 의 "(사용자 이름 포함)"."""

    username: str


@router.get("/teams/{teamId}/members", tags=["TEAM"], dependencies=[TeamMember])
async def list_team_members(team_id: TeamId, session: Session) -> dict[str, Any]:
    members = await teams.list_members(session, team_id)
    # 멤버 수만큼 조회하지 않는다. user_id 목록으로 한 번에 받는다.
    names = await auth.usernames_of(session, [m.user_id for m in members])
    return ok(
        [TeamMemberWithUsername(**m.model_dump(), username=names[m.user_id]) for m in members]
    )


@router.get("/projects/{projectId}/members", tags=["PRJ"], dependencies=[ProjectViewer])
async def list_project_members(project_id: ProjectId, session: Session) -> dict[str, Any]:
    members = await projects.list_members(session, project_id)
    names = await auth.usernames_of(session, [m.user_id for m in members])
    return ok(
        [ProjectMemberWithUsername(**m.model_dump(), username=names[m.user_id]) for m in members]
    )


@router.post(
    "/teams/{teamId}/invitations",
    status_code=status.HTTP_201_CREATED,
    tags=["TEAM"],
    dependencies=[TeamAdmin],
)
async def invite_to_team(
    team_id: TeamId, body: TeamInvitationCreate, session: Session, user: User
) -> dict[str, Any]:
    # 명세 §4.7: 이미 팀원이면 409. 이메일 → 사용자 조회가 AUTH 소관이라 여기서 막는다.
    invited = await auth.find_user_id_by_email(session, str(body.invited_email))
    if invited is not None and await teams.is_member(session, team_id, invited):
        raise errors.AlreadyTeamMember()
    return ok(await teams.invite(session, team_id, user, body))


@router.post(
    "/projects/{projectId}/invitations",
    status_code=status.HTTP_201_CREATED,
    tags=["PRJ"],
    dependencies=[ProjectOwner],
)
async def invite_to_project(
    project_id: ProjectId, body: ProjectInvitationCreate, session: Session, user: User
) -> dict[str, Any]:
    invited = await auth.find_user_id_by_email(session, str(body.invited_email))
    if invited is not None and await projects.is_member(session, project_id, invited):
        raise errors.AlreadyMember()
    return ok(await projects.invite(session, project_id, user, body))
