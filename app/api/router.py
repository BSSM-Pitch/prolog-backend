"""조합 레이어 — 두 모듈 이상의 데이터가 필요한 엔드포인트만 여기 둔다.

모듈끼리는 서로를 import 하지 못한다(규칙 3). 이 레이어는 모든 Ring 위에 있으므로
AUTH·TEAM·PRJ 를 각각 호출해 응답을 합칠 수 있다. `.importlinter` 의 `rings` 계약에
`api` 를 최상위 레이어로 등재해 두었으니 반대 방향(모듈 → api)은 기계가 막는다.

여기로 올라온 이유가 없는 엔드포인트는 모듈 라우터에 그대로 둔다.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.content.manuscripts import service as manuscripts
from app.core import errors
from app.core.deps import User, require_project_role, require_team_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import ok
from app.db.session import Session
from app.platform_.auth import service as auth
from app.platform_.projects import service as projects
from app.platform_.projects.schemas import InvitationCreate as ProjectInvitationCreate
from app.platform_.projects.schemas import (
    OwnerType,
    ProjectCreate,
    ProjectMemberResponse,
    ProjectResponse,
    ProjectUpdate,
)
from app.platform_.teams import service as teams
from app.platform_.teams.schemas import InvitationCreate as TeamInvitationCreate
from app.platform_.teams.schemas import TeamMemberResponse

router = APIRouter()

TeamId = Annotated[UUID, Path(alias="teamId")]
ProjectId = Annotated[UUID, Path(alias="projectId")]
TeamMember = Depends(require_team_role("member"))
TeamAdmin = Depends(require_team_role("admin"))
ProjectViewer = Depends(require_project_role("viewer"))
ProjectEditor = Depends(require_project_role("editor"))
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


@router.get("/teams/{teamId}/projects", tags=["TEAM"], dependencies=[TeamMember])
async def list_team_projects(
    team_id: TeamId,
    session: Session,
    user: User,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """TEAM 명세 §4.13 — PRJ `GET /projects?team_id=` 와 같은 데이터.

    TEAM 경로인데 PRJ 데이터를 돌려주므로 조합 레이어에 둔다.
    """
    rows = await projects.list_projects(
        session,
        user,
        limit,
        decode_cursor(cursor) if cursor else None,
        owner_type="team",
        team_id=team_id,
    )
    page, meta = next_cursor(rows, limit)
    return ok([ProjectResponse.model_validate(p, from_attributes=True) for p in page], meta)


# --- PRJ: Project 응답은 manuscript_count 때문에 두 모듈의 데이터가 필요하다 ---------
# content 는 Ring 2 라 platform_.projects 가 직접 셀 수 없다(규칙 1). 그래서 Project 를
# 돌려주는 엔드포인트는 모두 이 레이어에 있다. 필드 집합이 엔드포인트마다 달라지지 않게
# 읽기만 올리지 않고 생성·수정까지 함께 올렸다.


async def _with_count(session: Session, response: ProjectResponse) -> ProjectResponse:
    counts = await manuscripts.manuscript_counts(session, [response.project_id])
    return response.model_copy(update={"manuscript_count": counts.get(response.project_id, 0)})


@router.post("/projects", status_code=status.HTTP_201_CREATED, tags=["PRJ"])
async def create_project(body: ProjectCreate, session: Session, user: User) -> dict[str, Any]:
    return ok(await _with_count(session, await projects.create_project(session, user, body)))


@router.get("/projects", tags=["PRJ"])
async def list_projects(
    session: Session,
    user: User,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    owner_type: Annotated[OwnerType | None, Query()] = None,
    team_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    rows = await projects.list_projects(
        session, user, limit, decode_cursor(cursor) if cursor else None, owner_type, team_id
    )
    page, page_meta = next_cursor(rows, limit)
    counts = await manuscripts.manuscript_counts(session, [p.id for p in page])
    return ok(
        [
            ProjectResponse.model_validate(p, from_attributes=True).model_copy(
                update={"manuscript_count": counts.get(p.id, 0)}
            )
            for p in page
        ],
        page_meta,
    )


@router.get("/projects/{projectId}", tags=["PRJ"], dependencies=[ProjectViewer])
async def get_project(project_id: ProjectId, session: Session) -> dict[str, Any]:
    return ok(await _with_count(session, await projects.get_project(session, project_id)))


@router.patch("/projects/{projectId}", tags=["PRJ"], dependencies=[ProjectEditor])
async def update_project(
    project_id: ProjectId, body: ProjectUpdate, session: Session
) -> dict[str, Any]:
    return ok(await _with_count(session, await projects.update_project(session, project_id, body)))
