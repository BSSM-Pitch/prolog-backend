from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.core import errors
from app.core.deps import ProjectContext, User, require_project_role
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.platform_.projects import service
from app.platform_.projects.schemas import (
    ProjectInvitationAccept,
    ProjectMemberResponse,
    ProjectMemberUpdate,
)

router = APIRouter(prefix="/projects", tags=["PRJ"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
Owner = Depends(require_project_role("owner"))
# 본인 탈퇴가 있어 컨텍스트(역할·호출자)가 필요하다 (명세 §4.11).
ViewerCtx = Annotated[ProjectContext, Depends(require_project_role("viewer"))]


@router.delete(
    "/{projectId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Owner],
    summary="프로젝트를 삭제한다",
)
async def delete_project(project_id: ProjectId, session: Session) -> None:
    """owner 만. 원고·챕터 등 프로젝트에 딸린 데이터가 함께 지워지며 되돌릴 수 없다."""
    await service.delete_project(session, project_id)


@router.patch(
    "/{projectId}/members/{userId}",
    dependencies=[Owner],
    summary="프로젝트 멤버의 역할을 바꾼다",
    response_model=Envelope[ProjectMemberResponse],
    responses=raises(errors.MemberNotFound, errors.LastOwnerCannotLeave),
)
async def update_member_role(
    project_id: ProjectId,
    user_id: Annotated[UUID, Path(alias="userId")],
    body: ProjectMemberUpdate,
    session: Session,
) -> dict[str, Any]:
    """owner 만. 대상은 프로젝트에 직접 속한 멤버(`source = project`)다.

    팀을 통해 접근하는 멤버(`source = team`)는 프로젝트 멤버 행이 없어 `MEMBER_NOT_FOUND` 다.
    마지막 owner 를 다른 역할로 내릴 수는 없다.
    """
    return ok(await service.update_member_role(session, project_id, user_id, body.role))


@router.delete(
    "/{projectId}/members/{userId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="프로젝트 멤버를 내보내거나 프로젝트에서 나간다",
    responses=raises(errors.MemberNotFound, errors.LastOwnerCannotLeave),
)
async def remove_member(
    project_id: ProjectId,
    user_id: Annotated[UUID, Path(alias="userId")],
    session: Session,
    ctx: ViewerCtx,
) -> None:
    """`userId` 가 나 자신이면 탈퇴다(멤버 누구나). 다른 사람이면 owner 만 할 수 있다.

    팀을 통해 접근하는 멤버(`source = team`)는 여기서 뺄 수 없다 — 팀에서 내보낸다.
    마지막 owner 는 나갈 수 없다.
    """
    await service.remove_member(session, project_id, user_id, ctx)


@router.delete(
    "/{projectId}/invitations/{invitationId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Owner],
    summary="프로젝트 초대를 취소한다",
    responses=raises(errors.InvitationNotFound, errors.InvitationNotPending),
)
async def revoke_invitation(
    project_id: ProjectId,
    invitation_id: Annotated[UUID, Path(alias="invitationId")],
    session: Session,
) -> None:
    """owner 만. 대기 중(pending)인 초대만 취소할 수 있다."""
    await service.revoke_invitation(session, project_id, invitation_id)


# 수락은 아직 멤버가 아닌 사용자가 호출한다 → require_project_role 을 걸지 않는다.
@router.post(
    "/{projectId}/invitations/{invitationId}/accept",
    summary="프로젝트 초대를 수락한다",
    response_model=Envelope[ProjectMemberResponse],
    responses=raises(
        errors.InvitationNotFound,
        errors.InvitationNotPending,
        errors.InvitationExpired,
        errors.AlreadyMember,
    ),
)
async def accept_invitation(
    project_id: ProjectId,
    invitation_id: Annotated[UUID, Path(alias="invitationId")],
    body: ProjectInvitationAccept,
    session: Session,
    user: User,
) -> dict[str, Any]:
    """초대 링크의 토큰을 본문에 넣는다. 수락 권한은 **토큰 소지**다 — 초대받은 이메일과
    로그인한 계정의 이메일이 달라도 된다.

    토큰이 틀리면 초대 상태와 무관하게 `INVITATION_NOT_FOUND` 다. 응답은 새로 생긴 멤버다.
    """
    return ok(await service.accept_invitation(session, project_id, invitation_id, user, body.token))
