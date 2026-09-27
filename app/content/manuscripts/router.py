from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.content.manuscripts import service
from app.content.manuscripts.schemas import (
    ChapterCreate,
    ChapterUpdate,
    ManuscriptCreate,
    ManuscriptUpdate,
    UploadRequest,
)
from app.content.manuscripts.storage import Storage, storage
from app.core.deps import ProjectContext, require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import ok
from app.db.session import Session

router = APIRouter(prefix="/projects", tags=["MSU"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
ManuscriptId = Annotated[UUID, Path(alias="manuscriptId")]
ChapterId = Annotated[UUID, Path(alias="chapterId")]
# ASSUMPTION: 명세가 엔드포인트별 역할을 정하지 않았다. 읽기 viewer / 쓰기 editor 로 둔다
# (PRJ §4.4 의 "owner|editor 만 수정" 과 같은 선).
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
EditorCtx = Annotated[ProjectContext, Depends(require_project_role("editor"))]
StorageDep = Annotated[Storage, Depends(storage)]


@router.get("/{projectId}/manuscripts", dependencies=[Viewer])
async def list_manuscripts(
    project_id: ProjectId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    rows = await service.list_manuscripts(
        session, project_id, limit, decode_cursor(cursor) if cursor else None
    )
    page, meta = next_cursor(rows, limit)
    return ok(await service.to_responses(session, page), meta)


@router.post("/{projectId}/manuscripts", status_code=status.HTTP_201_CREATED, dependencies=[Editor])
async def create_manuscript(
    project_id: ProjectId, body: ManuscriptCreate, session: Session
) -> dict[str, Any]:
    return ok(await service.create(session, project_id, body))


@router.get("/{projectId}/manuscripts/{manuscriptId}", dependencies=[Viewer])
async def get_manuscript(
    project_id: ProjectId, manuscript_id: ManuscriptId, session: Session
) -> dict[str, Any]:
    return ok(await service.get(session, project_id, manuscript_id))


@router.patch("/{projectId}/manuscripts/{manuscriptId}", dependencies=[Editor])
async def update_manuscript(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    body: ManuscriptUpdate,
    session: Session,
) -> dict[str, Any]:
    return ok(await service.update(session, project_id, manuscript_id, body))


@router.delete(
    "/{projectId}/manuscripts/{manuscriptId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
)
async def delete_manuscript(
    project_id: ProjectId, manuscript_id: ManuscriptId, session: Session
) -> None:
    await service.delete(session, project_id, manuscript_id)


@router.post("/{projectId}/manuscripts/{manuscriptId}/file", status_code=status.HTTP_202_ACCEPTED)
async def request_upload(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    body: UploadRequest,
    session: Session,
    ctx: EditorCtx,
    store: StorageDep,
) -> dict[str, Any]:
    """presigned URL 발급 — multipart 가 아니다. 클라이언트가 S3 로 직접 올린다."""
    return ok(
        await service.request_upload(session, project_id, manuscript_id, body, store, ctx.user.id)
    )


@router.post("/{projectId}/manuscripts/{manuscriptId}/file/complete")
async def complete_upload(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    session: Session,
    ctx: EditorCtx,
    store: StorageDep,
) -> dict[str, Any]:
    """클라이언트가 S3 PUT 을 마쳤다고 알린다. 객체를 확인하고 추출 잡을 건다."""
    return ok(await service.complete_upload(session, project_id, manuscript_id, store, ctx.user.id))


# --- 챕터: 프로젝트 직속이다 (SCDS 가 그렇게 참조한다) ----------------------


@router.get("/{projectId}/chapters", dependencies=[Viewer])
async def list_chapters(
    project_id: ProjectId,
    session: Session,
    manuscript_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    return ok(await service.list_chapters(session, project_id, manuscript_id))


@router.post("/{projectId}/chapters", status_code=status.HTTP_201_CREATED, dependencies=[Editor])
async def create_chapter(
    project_id: ProjectId, body: ChapterCreate, session: Session
) -> dict[str, Any]:
    return ok(await service.create_chapter(session, project_id, body))


@router.get("/{projectId}/chapters/{chapterId}", dependencies=[Viewer])
async def get_chapter(
    project_id: ProjectId, chapter_id: ChapterId, session: Session
) -> dict[str, Any]:
    return ok(await service.get_chapter(session, project_id, chapter_id))


@router.patch("/{projectId}/chapters/{chapterId}", dependencies=[Editor])
async def update_chapter(
    project_id: ProjectId, chapter_id: ChapterId, body: ChapterUpdate, session: Session
) -> dict[str, Any]:
    return ok(await service.update_chapter(session, project_id, chapter_id, body))


@router.delete(
    "/{projectId}/chapters/{chapterId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
)
async def delete_chapter(project_id: ProjectId, chapter_id: ChapterId, session: Session) -> None:
    await service.delete_chapter(session, project_id, chapter_id)
