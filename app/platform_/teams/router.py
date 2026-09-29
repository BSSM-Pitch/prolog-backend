from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.core import errors
from app.core.deps import User, require_team_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, Page, ok, raises
from app.db.session import Session
from app.platform_.teams import service
from app.platform_.teams.schemas import (
    TeamCreate,
    TeamInvitationAccept,
    TeamInvitationResponse,
    TeamMemberResponse,
    TeamMemberUpdate,
    TeamResponse,
    TeamUpdate,
)

router = APIRouter(prefix="/teams", tags=["TEAM"])

TeamId = Annotated[UUID, Path(alias="teamId")]
Member = Depends(require_team_role("member"))
# 본인 탈퇴가 있어 role 값 자체가 필요하다 (명세 §4.12).
MemberRole = Annotated[str, Depends(require_team_role("member"))]
Admin = Depends(require_team_role("admin"))
Owner = Depends(require_team_role("owner"))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="팀을 만든다",
    response_model=Envelope[TeamResponse],
)
async def create_team(body: TeamCreate, session: Session, user: User) -> dict[str, Any]:
    """만든 사람이 팀의 owner 가 된다."""
    return ok(await service.create_team(session, user, body))


@router.get("", summary="내가 속한 팀 목록", response_model=Page[TeamResponse])
async def list_teams(
    session: Session,
    user: User,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """최근에 만들어진 팀부터. 커서 페이지네이션이다."""
    rows = await service.list_teams(session, user, limit, decode_cursor(cursor) if cursor else None)
    page, meta = next_cursor(rows, limit)
    return ok(await service.to_responses(session, page), meta)


@router.get(
    "/{teamId}",
    dependencies=[Member],
    summary="팀을 조회한다",
    response_model=Envelope[TeamResponse],
)
async def get_team(team_id: TeamId, session: Session) -> dict[str, Any]:
    """팀원만 볼 수 있다. `member_count` 는 조회 시점의 인원이다."""
    return ok(await service.get_team(session, team_id))


@router.patch(
    "/{teamId}",
    dependencies=[Admin],
    summary="팀 정보를 수정한다",
    response_model=Envelope[TeamResponse],
)
async def update_team(team_id: TeamId, body: TeamUpdate, session: Session) -> dict[str, Any]:
    """owner · admin 만. 보낸 필드만 바뀐다."""
    return ok(await service.update_team(session, team_id, body))


@router.delete(
    "/{teamId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Owner],
    summary="팀을 삭제한다",
    responses=raises(errors.TeamHasActiveProjects),
)
async def delete_team(team_id: TeamId, session: Session) -> None:
    """owner 만. 팀 프로젝트가 하나라도 남아 있으면 `TEAM_HAS_ACTIVE_PROJECTS` 로 거부된다."""
    await service.delete_team(session, team_id)


@router.patch(
    "/{teamId}/members/{userId}",
    dependencies=[Owner],
    summary="팀원의 역할을 바꾼다",
    response_model=Envelope[TeamMemberResponse],
    responses=raises(errors.TeamMemberNotFound, errors.LastOwnerCannotLeave),
)
async def update_member_role(
    team_id: TeamId,
    user_id: Annotated[UUID, Path(alias="userId")],
    body: TeamMemberUpdate,
    session: Session,
) -> dict[str, Any]:
    """owner 만. 마지막 owner 를 다른 역할로 내릴 수는 없다."""
    return ok(await service.update_member_role(session, team_id, user_id, body.role))


@router.delete(
    "/{teamId}/members/{userId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="팀원을 내보내거나 팀에서 나간다",
    responses=raises(errors.TeamMemberNotFound, errors.LastOwnerCannotLeave),
)
async def remove_member(
    team_id: TeamId,
    user_id: Annotated[UUID, Path(alias="userId")],
    session: Session,
    user: User,
    role: MemberRole,
) -> None:
    """`userId` 가 나 자신이면 탈퇴다(팀원 누구나). 다른 사람이면 owner · admin 만 할 수 있다.

    마지막 owner 는 나갈 수 없다.
    """
    await service.remove_member(session, team_id, user_id, user, role)


@router.get(
    "/{teamId}/invitations",
    dependencies=[Admin],
    summary="팀 초대 목록",
    response_model=Envelope[list[TeamInvitationResponse]],
)
async def list_invitations(team_id: TeamId, session: Session) -> dict[str, Any]:
    """owner · admin 만. 모든 상태(pending · accepted · revoked)를 최근 순으로 준다.

    원문 토큰은 들어 있지 않다 — 토큰은 초대 생성 응답에서 한 번만 받을 수 있다.
    """
    return ok(await service.list_invitations(session, team_id))


@router.delete(
    "/{teamId}/invitations/{invitationId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Admin],
    summary="팀 초대를 취소한다",
    responses=raises(errors.TeamInvitationNotFound, errors.InvitationNotPending),
)
async def revoke_invitation(
    team_id: TeamId,
    invitation_id: Annotated[UUID, Path(alias="invitationId")],
    session: Session,
) -> None:
    """owner · admin 만. 대기 중(pending)인 초대만 취소할 수 있다.

    취소하면 같은 주소로 다시 초대할 수 있다.
    """
    await service.revoke_invitation(session, team_id, invitation_id)


# 초대 수락은 아직 멤버가 아닌 사용자가 호출한다 → require_team_role 로 막을 수 없다.
@router.post(
    "/{teamId}/invitations/{invitationId}/accept",
    summary="팀 초대를 수락한다",
    response_model=Envelope[TeamMemberResponse],
    responses=raises(
        errors.TeamInvitationNotFound,
        errors.InvitationNotPending,
        errors.InvitationExpired,
        errors.AlreadyTeamMember,
    ),
)
async def accept_invitation(
    team_id: TeamId,
    invitation_id: Annotated[UUID, Path(alias="invitationId")],
    body: TeamInvitationAccept,
    session: Session,
    user: User,
) -> dict[str, Any]:
    """초대 링크의 토큰을 본문에 넣는다. 수락 권한은 **토큰 소지**다 — 초대받은 이메일과
    로그인한 계정의 이메일이 달라도 된다.

    토큰이 틀리면 초대 상태와 무관하게 `TEAM_INVITATION_NOT_FOUND` 다. 응답은 새로 생긴 팀원이다.
    """
    return ok(await service.accept_invitation(session, team_id, invitation_id, user, body.token))
