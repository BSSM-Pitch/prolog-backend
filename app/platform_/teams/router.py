from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.core.deps import User, require_team_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import ok
from app.db.session import Session
from app.platform_.teams import service
from app.platform_.teams.schemas import InvitationAccept, TeamCreate, TeamMemberUpdate, TeamUpdate

router = APIRouter(prefix="/teams", tags=["TEAM"])

TeamId = Annotated[UUID, Path(alias="teamId")]
Member = Depends(require_team_role("member"))
# 본인 탈퇴가 있어 role 값 자체가 필요하다 (명세 §4.12).
MemberRole = Annotated[str, Depends(require_team_role("member"))]
Admin = Depends(require_team_role("admin"))
Owner = Depends(require_team_role("owner"))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_team(body: TeamCreate, session: Session, user: User) -> dict[str, Any]:
    return ok(await service.create_team(session, user, body))


@router.get("")
async def list_teams(
    session: Session,
    user: User,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    rows = await service.list_teams(session, user, limit, decode_cursor(cursor) if cursor else None)
    page, meta = next_cursor(rows, limit)
    return ok(await service.to_responses(session, page), meta)


@router.get("/{teamId}", dependencies=[Member])
async def get_team(team_id: TeamId, session: Session) -> dict[str, Any]:
    return ok(await service.get_team(session, team_id))


@router.patch("/{teamId}", dependencies=[Admin])
async def update_team(team_id: TeamId, body: TeamUpdate, session: Session) -> dict[str, Any]:
    return ok(await service.update_team(session, team_id, body))


@router.delete("/{teamId}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Owner])
async def delete_team(team_id: TeamId, session: Session) -> None:
    await service.delete_team(session, team_id)


@router.patch("/{teamId}/members/{userId}", dependencies=[Owner])
async def update_member_role(
    team_id: TeamId,
    user_id: Annotated[UUID, Path(alias="userId")],
    body: TeamMemberUpdate,
    session: Session,
) -> dict[str, Any]:
    return ok(await service.update_member_role(session, team_id, user_id, body.role))


@router.delete("/{teamId}/members/{userId}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    team_id: TeamId,
    user_id: Annotated[UUID, Path(alias="userId")],
    session: Session,
    user: User,
    role: MemberRole,
) -> None:
    await service.remove_member(session, team_id, user_id, user, role)


@router.get("/{teamId}/invitations", dependencies=[Admin])
async def list_invitations(team_id: TeamId, session: Session) -> dict[str, Any]:
    return ok(await service.list_invitations(session, team_id))


@router.delete(
    "/{teamId}/invitations/{invitationId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Admin],
)
async def revoke_invitation(
    team_id: TeamId,
    invitation_id: Annotated[UUID, Path(alias="invitationId")],
    session: Session,
) -> None:
    await service.revoke_invitation(session, team_id, invitation_id)


# 초대 수락은 아직 멤버가 아닌 사용자가 호출한다 → require_team_role 로 막을 수 없다.
@router.post("/{teamId}/invitations/{invitationId}/accept")
async def accept_invitation(
    team_id: TeamId,
    invitation_id: Annotated[UUID, Path(alias="invitationId")],
    body: InvitationAccept,
    session: Session,
    user: User,
) -> dict[str, Any]:
    return ok(await service.accept_invitation(session, team_id, invitation_id, user, body.token))
