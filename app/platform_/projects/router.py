from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.core.deps import ProjectContext, User, require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import ok
from app.db.session import Session
from app.platform_.projects import service
from app.platform_.projects.schemas import (
    OwnerType,
    ProjectCreate,
    ProjectMemberUpdate,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["PRJ"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
Owner = Depends(require_project_role("owner"))
# 본인 탈퇴가 있어 컨텍스트(역할·호출자)가 필요하다 (명세 §4.11).
ViewerCtx = Annotated[ProjectContext, Depends(require_project_role("viewer"))]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, session: Session, user: User) -> dict[str, Any]:
    return ok(await service.create_project(session, user, body))


@router.get("")
async def list_projects(
    session: Session,
    user: User,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    owner_type: Annotated[OwnerType | None, Query()] = None,
    team_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    rows = await service.list_projects(
        session, user, limit, decode_cursor(cursor) if cursor else None, owner_type, team_id
    )
    page, meta = next_cursor(rows, limit)
    return ok([ProjectResponse.model_validate(p, from_attributes=True) for p in page], meta)


@router.get("/{projectId}", dependencies=[Viewer])
async def get_project(project_id: ProjectId, session: Session) -> dict[str, Any]:
    return ok(await service.get_project(session, project_id))


@router.patch("/{projectId}", dependencies=[Editor])
async def update_project(
    project_id: ProjectId, body: ProjectUpdate, session: Session
) -> dict[str, Any]:
    return ok(await service.update_project(session, project_id, body))


@router.delete("/{projectId}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Owner])
async def delete_project(project_id: ProjectId, session: Session) -> None:
    await service.delete_project(session, project_id)


@router.patch("/{projectId}/members/{userId}", dependencies=[Owner])
async def update_member_role(
    project_id: ProjectId,
    user_id: Annotated[UUID, Path(alias="userId")],
    body: ProjectMemberUpdate,
    session: Session,
) -> dict[str, Any]:
    return ok(await service.update_member_role(session, project_id, user_id, body.role))


@router.delete("/{projectId}/members/{userId}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: ProjectId,
    user_id: Annotated[UUID, Path(alias="userId")],
    session: Session,
    ctx: ViewerCtx,
) -> None:
    await service.remove_member(session, project_id, user_id, ctx)


@router.get("/{projectId}/invitations", dependencies=[Owner])
async def list_invitations(project_id: ProjectId, session: Session) -> dict[str, Any]:
    return ok(await service.list_invitations(session, project_id))


@router.delete(
    "/{projectId}/invitations/{invitationId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Owner],
)
async def revoke_invitation(
    project_id: ProjectId,
    invitation_id: Annotated[UUID, Path(alias="invitationId")],
    session: Session,
) -> None:
    await service.revoke_invitation(session, project_id, invitation_id)


# 수락/거절은 아직 멤버가 아닌 사용자가 호출한다 → require_project_role 을 걸지 않는다.
@router.post("/{projectId}/invitations/{invitationId}/accept")
async def accept_invitation(
    project_id: ProjectId,
    invitation_id: Annotated[UUID, Path(alias="invitationId")],
    session: Session,
    user: User,
) -> dict[str, Any]:
    return ok(await service.accept_invitation(session, project_id, invitation_id, user))


@router.post("/{projectId}/invitations/{invitationId}/reject")
async def reject_invitation(
    project_id: ProjectId,
    invitation_id: Annotated[UUID, Path(alias="invitationId")],
    session: Session,
    user: User,
) -> dict[str, Any]:
    return ok(await service.reject_invitation(session, project_id, invitation_id, user))
