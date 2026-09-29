"""조합 레이어 — 두 모듈 이상의 데이터가 필요한 엔드포인트만 여기 둔다.

모듈끼리는 서로를 import 하지 못한다(규칙 3). 이 레이어는 모든 Ring 위에 있으므로
AUTH·TEAM·PRJ 를 각각 호출해 응답을 합칠 수 있다. `.importlinter` 의 `rings` 계약에
`api` 를 최상위 레이어로 등재해 두었으니 반대 방향(모듈 → api)은 기계가 막는다.

여기로 올라온 이유가 없는 엔드포인트는 모듈 라우터에 그대로 둔다.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import Field

from app.content.manuscripts import service as manuscripts
from app.core import errors
from app.core.deps import User, effective_project_role, require_project_role, require_team_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, Page, ok, raises
from app.db.session import Session
from app.platform_.auth import service as auth
from app.platform_.projects import service as projects
from app.platform_.projects.schemas import (
    OwnerType,
    ProjectCreate,
    ProjectInvitationCreate,
    ProjectInvitationCreatedResponse,
    ProjectMemberResponse,
    ProjectResponse,
    ProjectRole,
    ProjectUpdate,
)
from app.platform_.teams import service as teams
from app.platform_.teams.schemas import (
    TeamInvitationCreate,
    TeamInvitationCreatedResponse,
    TeamMemberResponse,
)

router = APIRouter()

TeamId = Annotated[UUID, Path(alias="teamId")]
ProjectId = Annotated[UUID, Path(alias="projectId")]
TeamMember = Depends(require_team_role("member"))
TeamAdmin = Depends(require_team_role("admin"))
ProjectViewer = Depends(require_project_role("viewer"))
ProjectEditor = Depends(require_project_role("editor"))
ProjectOwner = Depends(require_project_role("owner"))

MemberSource = Literal["project", "team"]


class TeamMemberWithUsername(TeamMemberResponse):
    """TEAM 명세 §4.6 의 "(사용자 이름 포함)". 필요한 것은 username 하나다(§6.3)."""

    username: str


class ProjectMemberWithUsername(ProjectMemberResponse):
    """PRJ 명세 §4.6 의 "(사용자 이름 포함)" + 멤버십의 출처."""

    username: str
    # ASSUMPTION: 명세에 없는 필드다. 팀 프로젝트의 팀원은 project_members 행 없이 팀 소속만으로
    # 작업한다(P0). 목록에 넣되 행이 있는 멤버와 구분해야 역할 변경·내보내기를 어디서 할지
    # 화면이 안다. 제안 목록(CLAUDE.md §9)에 있다.
    source: MemberSource = Field(
        description=(
            "`project`: 프로젝트에 직접 속한 멤버 — 이 API 로 역할 변경·내보내기를 한다. "
            "`team`: 팀 프로젝트의 팀원이라 접근하는 멤버 — 역할은 항상 editor 이고 "
            "`joined_at` 은 팀 가입 시각이다. 역할 변경·내보내기는 팀에서 한다."
        )
    )


@router.get(
    "/teams/{teamId}/members",
    tags=["TEAM"],
    dependencies=[TeamMember],
    summary="팀원 목록",
    response_model=Page[TeamMemberWithUsername],
)
async def list_team_members(
    team_id: TeamId, session: Session, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> dict[str, Any]:
    """팀원만 볼 수 있다. 가입 순(`joined_at` 오름차순). 커서 페이지네이션이다."""
    rows = await teams.list_members(
        session, team_id, limit, decode_cursor(cursor) if cursor else None
    )
    page, meta = next_cursor(rows, limit, key=lambda m: (m.joined_at, m.user_id))
    # 멤버 수만큼 조회하지 않는다. user_id 목록으로 한 번에 받는다.
    names = await auth.usernames_of(session, [m.user_id for m in page])
    return ok(
        [TeamMemberWithUsername(**m.model_dump(), username=names[m.user_id]) for m in page], meta
    )


async def _project_members(
    session: Session, project_id: UUID
) -> list[tuple[ProjectMemberResponse, MemberSource]]:
    """명시적 멤버 + (팀 프로젝트면) 팀원. 한 사람은 한 번만, 권한 판정과 같은 규칙으로.

    두 출처가 각기 다른 모듈의 테이블이라 SQL 한 번으로 합칠 수 없다(규칙 3). 둘 다 전원을
    읽어 메모리에서 합친다.
    """
    explicit = await projects.list_members(session, project_id)
    team_id = (await projects.get_project(session, project_id)).team_id
    team = await teams.list_members(session, team_id) if team_id is not None else []

    merged: dict[UUID, tuple[ProjectMemberResponse, MemberSource]] = {
        m.user_id: (m, "project") for m in explicit
    }
    for t in team:
        current = merged.get(t.user_id)
        role = effective_project_role(current[0].role if current else None, is_team_member=True)
        if current is None or role != current[0].role:
            # 팀이 주는 role 이 이긴다 → 이 사람의 실제 권한은 팀에서 온다.
            member = ProjectMemberResponse(
                project_id=project_id,
                user_id=t.user_id,
                role=cast(ProjectRole, role),  # 팀원에게는 None 이 나오지 않는다
                joined_at=t.joined_at,
            )
            merged[t.user_id] = (member, "team")
    return list(merged.values())


@router.get(
    "/projects/{projectId}/members",
    tags=["PRJ"],
    dependencies=[ProjectViewer],
    summary="프로젝트 멤버 목록",
    response_model=Page[ProjectMemberWithUsername],
)
async def list_project_members(
    project_id: ProjectId, session: Session, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> dict[str, Any]:
    """프로젝트의 viewer 이상. `joined_at` 오름차순. 커서 페이지네이션이다.

    팀 프로젝트면 **팀원도 포함한다**(`source = team`). 실제로 editor 권한으로 작업할 수
    있는 사람이 목록에 빠지지 않게 하기 위해서다. 한 사람은 한 번만 나오고, `role` 은
    그 사람이 실제로 가진 권한이다.
    """
    # ponytail: 두 출처 전원을 읽어 메모리에서 자른다. 프로젝트 인원은 팀 규모(수십)라
    # 문제없다. 수천이 되면 platform 을 공유 커널로 올리고 SQL UNION 키셋으로 바꾼다.
    rows = sorted(await _project_members(session, project_id), key=lambda r: _member_key(r[0]))
    if cursor:
        after = decode_cursor(cursor)
        rows = [r for r in rows if _member_key(r[0]) > after]
    page, meta = next_cursor(rows[: limit + 1], limit, key=lambda r: _member_key(r[0]))
    names = await auth.usernames_of(session, [m.user_id for m, _ in page])
    return ok(
        [
            ProjectMemberWithUsername(**m.model_dump(), username=names[m.user_id], source=source)
            for m, source in page
        ],
        meta,
    )


def _member_key(member: ProjectMemberResponse) -> tuple[datetime, UUID]:
    return member.joined_at, member.user_id


@router.post(
    "/teams/{teamId}/invitations",
    status_code=status.HTTP_201_CREATED,
    tags=["TEAM"],
    dependencies=[TeamAdmin],
    summary="팀에 초대한다",
    response_model=Envelope[TeamInvitationCreatedResponse],
    responses=raises(errors.AlreadyTeamMember, errors.DuplicateInvitation),
)
async def invite_to_team(
    team_id: TeamId, body: TeamInvitationCreate, session: Session, user: User
) -> dict[str, Any]:
    """owner · admin 만. 이메일로 초대한다(가입하지 않은 주소도 된다). owner 로는 초대할 수 없다.

    **응답의 `token` 은 이때 한 번만 받을 수 있다.** 서버는 원문을 저장하지 않는다.
    초대 링크에 담아 전달하고, 받는 사람은 `.../accept` 에 이 토큰을 보낸다.
    같은 주소에 대기 중인 초대가 있으면 `DUPLICATE_INVITATION` 이다.
    """
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
    summary="프로젝트에 초대한다",
    response_model=Envelope[ProjectInvitationCreatedResponse],
    responses=raises(errors.AlreadyMember, errors.DuplicateInvitation),
)
async def invite_to_project(
    project_id: ProjectId, body: ProjectInvitationCreate, session: Session, user: User
) -> dict[str, Any]:
    """owner 만. 이메일로 editor 또는 viewer 로 초대한다. owner 로는 초대할 수 없다.

    **응답의 `token` 은 이때 한 번만 받을 수 있다.** 서버는 원문을 저장하지 않는다.
    같은 주소에 대기 중인 초대가 있으면 `DUPLICATE_INVITATION` 이다.
    """
    invited = await auth.find_user_id_by_email(session, str(body.invited_email))
    if invited is not None and await projects.is_member(session, project_id, invited):
        raise errors.AlreadyMember()
    return ok(await projects.invite(session, project_id, user, body))


@router.get(
    "/teams/{teamId}/projects",
    tags=["TEAM"],
    dependencies=[TeamMember],
    summary="팀의 프로젝트 목록",
    response_model=Page[ProjectResponse],
)
async def list_team_projects(
    team_id: TeamId,
    session: Session,
    user: User,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """팀원만. `GET /projects?team_id=` 와 같은 데이터다. 최근에 만든 프로젝트부터."""
    # TEAM 명세 §4.13. TEAM 경로인데 PRJ 데이터를 돌려주므로 조합 레이어에 둔다.
    rows = await projects.list_projects(
        session,
        user,
        limit,
        decode_cursor(cursor) if cursor else None,
        owner_type="team",
        team_id=team_id,
    )
    page, meta = next_cursor(rows, limit)
    counts = await manuscripts.manuscript_counts(session, [p.id for p in page])
    return ok(
        [
            ProjectResponse.model_validate(p, from_attributes=True).model_copy(
                update={"manuscript_count": counts.get(p.id, 0)}
            )
            for p in page
        ],
        meta,
    )


# --- PRJ: Project 응답은 manuscript_count 때문에 두 모듈의 데이터가 필요하다 ---------
# content 는 Ring 2 라 platform_.projects 가 직접 셀 수 없다(규칙 1). 그래서 Project 를
# 돌려주는 엔드포인트는 모두 이 레이어에 있다. 필드 집합이 엔드포인트마다 달라지지 않게
# 읽기만 올리지 않고 생성·수정까지 함께 올렸다.


async def _with_count(session: Session, response: ProjectResponse) -> ProjectResponse:
    counts = await manuscripts.manuscript_counts(session, [response.project_id])
    return response.model_copy(update={"manuscript_count": counts.get(response.project_id, 0)})


@router.post(
    "/projects",
    status_code=status.HTTP_201_CREATED,
    tags=["PRJ"],
    summary="프로젝트를 만든다",
    response_model=Envelope[ProjectResponse],
    responses=raises(errors.NotTeamMember, errors.TeamNotFound),
)
async def create_project(body: ProjectCreate, session: Session, user: User) -> dict[str, Any]:
    """만든 사람이 owner 가 된다.

    - 개인 프로젝트: `owner_type = personal`, `team_id` 없음.
    - 팀 프로젝트: `owner_type = team` + `team_id`. 그 팀의 팀원이면 누구나 만들 수 있고,
      팀원 전원이 editor 로 접근한다. 팀원이 아니면 `NOT_TEAM_MEMBER` 다.

    `owner_type` 과 `team_id` 가 맞지 않으면 `INVALID_INPUT` 이다.
    """
    return ok(await _with_count(session, await projects.create_project(session, user, body)))


@router.get(
    "/projects",
    tags=["PRJ"],
    summary="내 프로젝트 목록",
    response_model=Page[ProjectResponse],
)
async def list_projects(
    session: Session,
    user: User,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    owner_type: Annotated[OwnerType | None, Query(description="소유 형태로 거른다")] = None,
    team_id: Annotated[UUID | None, Query(description="이 팀의 프로젝트만 본다")] = None,
) -> dict[str, Any]:
    """내가 직접 속한 프로젝트 + 내가 속한 팀의 팀 프로젝트. 최근에 만든 것부터."""
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


@router.get(
    "/projects/{projectId}",
    tags=["PRJ"],
    dependencies=[ProjectViewer],
    summary="프로젝트를 조회한다",
    response_model=Envelope[ProjectResponse],
)
async def get_project(project_id: ProjectId, session: Session) -> dict[str, Any]:
    """프로젝트의 viewer 이상. 팀 프로젝트는 팀원이면 볼 수 있다."""
    return ok(await _with_count(session, await projects.get_project(session, project_id)))


@router.patch(
    "/projects/{projectId}",
    tags=["PRJ"],
    dependencies=[ProjectEditor],
    summary="프로젝트 정보를 수정한다",
    response_model=Envelope[ProjectResponse],
)
async def update_project(
    project_id: ProjectId, body: ProjectUpdate, session: Session
) -> dict[str, Any]:
    """프로젝트의 editor 이상. 보낸 필드만 바뀐다."""
    return ok(await _with_count(session, await projects.update_project(session, project_id, body)))
