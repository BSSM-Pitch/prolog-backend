from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.content.manuscripts import service
from app.content.manuscripts.schemas import (
    ChapterCreate,
    ChapterResponse,
    ChapterUpdate,
    ManuscriptCreate,
    ManuscriptResponse,
    ManuscriptUpdate,
    ManuscriptVersionResponse,
    UploadRequest,
    UploadResponse,
)
from app.content.manuscripts.storage import Storage, storage
from app.core import errors
from app.core.deps import ProjectContext, require_project_role
from app.core.pagination import (
    DEFAULT_LIMIT,
    Cursor,
    Limit,
    decode_cursor,
    decode_int_cursor,
    next_cursor,
)
from app.core.response import Envelope, Page, ok, raises
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

ManuscriptMissing = raises(errors.ManuscriptNotFound)
ChapterMissing = raises(errors.ChapterNotFound)


@router.get(
    "/{projectId}/manuscripts",
    dependencies=[Viewer],
    summary="프로젝트의 원고 목록",
    response_model=Page[ManuscriptResponse],
)
async def list_manuscripts(
    project_id: ProjectId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """최근에 만든 원고부터. 커서 페이지네이션이다. 프로젝트의 viewer 이상."""
    rows = await service.list_manuscripts(
        session, project_id, limit, decode_cursor(cursor) if cursor else None
    )
    page, meta = next_cursor(rows, limit)
    return ok(await service.to_responses(session, page), meta)


@router.post(
    "/{projectId}/manuscripts",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Editor],
    summary="원고를 만든다",
    response_model=Envelope[ManuscriptResponse],
)
async def create_manuscript(
    project_id: ProjectId, body: ManuscriptCreate, session: Session
) -> dict[str, Any]:
    """프로젝트의 editor 이상.

    - `source_type = editor`: 빈 본문으로 바로 `ready` 다. 본문은 `PATCH` 로 쓴다.
    - `source_type = upload`: `draft` 로 만들어진다. 파일 업로드 절차는
      `POST .../file` → S3 PUT → `POST .../file/complete` 순서다.

    `source_type` 은 만든 뒤 바꿀 수 없다.
    """
    return ok(await service.create(session, project_id, body))


@router.get(
    "/{projectId}/manuscripts/{manuscriptId}",
    dependencies=[Viewer],
    summary="원고를 조회한다",
    response_model=Envelope[ManuscriptResponse],
    responses=ManuscriptMissing,
)
async def get_manuscript(
    project_id: ProjectId, manuscript_id: ManuscriptId, session: Session
) -> dict[str, Any]:
    """본문(`content`)을 포함한다. 업로드 원고는 추출이 끝나기 전까지 `content = null` 이다.

    추출 진행은 `status` 로 본다: `draft` → `processing` → `ready` | `failed`.
    """
    return ok(await service.get(session, project_id, manuscript_id))


@router.patch(
    "/{projectId}/manuscripts/{manuscriptId}",
    summary="원고의 제목이나 본문을 수정한다",
    response_model=Envelope[ManuscriptResponse],
    responses=raises(errors.ManuscriptNotFound, errors.SourceTypeImmutable),
)
async def update_manuscript(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    body: ManuscriptUpdate,
    session: Session,
    ctx: EditorCtx,
) -> dict[str, Any]:
    """프로젝트의 editor 이상. 보낸 필드만 바뀐다.

    `source_type` 을 다른 값으로 보내면 `SOURCE_TYPE_IMMUTABLE` 이다. 본문이 바뀌면 편집 이력에
    남는다 — 같은 5분 창 안의 작은 저장(길이 변화 1,000자 미만)은 한 스냅샷으로 묶인다.
    """
    return ok(await service.update(session, project_id, manuscript_id, body, ctx.user.id))


@router.get(
    "/{projectId}/manuscripts/{manuscriptId}/versions",
    dependencies=[Viewer],
    summary="원고 편집 이력",
    response_model=Page[ManuscriptVersionResponse],
    responses=ManuscriptMissing,
)
async def list_versions(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """최신 스냅샷부터. 커서 페이지네이션이다. 스냅샷마다 본문 전문이 들어 있다.

    `source`: `upload` 는 파일 추출 결과(항상 새 스냅샷), `editor` 는 편집기 저장이다.
    편집기 저장은 마지막 편집기 스냅샷으로부터 5분이 지났거나 길이가 1,000자 이상 바뀌면 새
    스냅샷이 되고, 아니면 마지막 스냅샷을 덮어쓴다(`updated_at` 이 바뀐다).
    """
    rows = await service.list_versions(
        session, project_id, manuscript_id, limit, decode_int_cursor(cursor) if cursor else None
    )
    page, meta = next_cursor(rows, limit, key=lambda v: (v.version_no, v.id))
    return ok(service.to_version_responses(page), meta)


@router.delete(
    "/{projectId}/manuscripts/{manuscriptId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
    summary="원고를 삭제한다",
    responses=ManuscriptMissing,
)
async def delete_manuscript(
    project_id: ProjectId, manuscript_id: ManuscriptId, session: Session
) -> None:
    """프로젝트의 editor 이상. 되돌릴 수 없다."""
    await service.delete(session, project_id, manuscript_id)


@router.post(
    "/{projectId}/manuscripts/{manuscriptId}/file",
    status_code=status.HTTP_202_ACCEPTED,
    summary="원고 파일 업로드 URL 을 발급한다",
    response_model=Envelope[UploadResponse],
    responses=raises(errors.ManuscriptNotFound, errors.UnsupportedFileFormat),
)
async def request_upload(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    body: UploadRequest,
    session: Session,
    ctx: EditorCtx,
    store: StorageDep,
) -> dict[str, Any]:
    """프로젝트의 editor 이상. `source_type = upload` 원고만 된다(아니면 `INVALID_INPUT`).

    파일은 서버를 거치지 않는다. 응답의 `upload_url` 로 **클라이언트가 S3 에 직접 PUT** 한다
    (`expires_in` 초 안에, `Content-Type` 은 형식에 맞게). 업로드를 마치면
    `POST .../file/complete` 를 불러야 추출이 시작된다. 지원 형식이 아니면
    `UNSUPPORTED_FILE_FORMAT` 이고 `details.supported` 에 지원 목록이 온다.
    """
    return ok(
        await service.request_upload(session, project_id, manuscript_id, body, store, ctx.user.id)
    )


@router.post(
    "/{projectId}/manuscripts/{manuscriptId}/file/complete",
    summary="파일 업로드를 마쳤다고 알린다",
    response_model=Envelope[ManuscriptResponse],
    responses=ManuscriptMissing,
)
async def complete_upload(
    project_id: ProjectId,
    manuscript_id: ManuscriptId,
    session: Session,
    ctx: EditorCtx,
    store: StorageDep,
) -> dict[str, Any]:
    """S3 PUT 이 끝난 뒤 부른다. 서버가 파일을 확인하고 텍스트 추출을 시작한다.

    응답의 `status` 는 `processing` 이다. 이후 원고 조회로 `ready` | `failed` 를 확인한다.
    두 번 불러도 추출은 한 번만 걸린다. 업로드 URL 을 먼저 발급받지 않았거나 파일이 아직
    없으면 `INVALID_INPUT` 이다.
    """
    return ok(await service.complete_upload(session, project_id, manuscript_id, store, ctx.user.id))


# --- 챕터: 프로젝트 직속이다 (SCDS 가 그렇게 참조한다) ----------------------


@router.get(
    "/{projectId}/chapters",
    dependencies=[Viewer],
    summary="프로젝트의 챕터 목록",
    response_model=Page[ChapterResponse],
)
async def list_chapters(
    project_id: ProjectId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    manuscript_id: Annotated[UUID | None, Query(description="이 원고의 챕터만 본다")] = None,
) -> dict[str, Any]:
    """화 번호(`chapter_no`) 오름차순. 커서 페이지네이션이다. 프로젝트의 viewer 이상."""
    rows = await service.list_chapters(
        session,
        project_id,
        limit,
        decode_int_cursor(cursor) if cursor else None,
        manuscript_id,
    )
    page, meta = next_cursor(rows, limit, key=lambda c: (c.chapter_no, c.id))
    return ok(service.to_chapter_responses(page), meta)


@router.post(
    "/{projectId}/chapters",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Editor],
    summary="챕터를 만든다",
    response_model=Envelope[ChapterResponse],
    responses=ManuscriptMissing,
)
async def create_chapter(
    project_id: ProjectId, body: ChapterCreate, session: Session
) -> dict[str, Any]:
    """프로젝트의 editor 이상. `manuscript_id` 는 같은 프로젝트의 원고여야 한다.

    같은 원고에 이미 있는 `chapter_no` 면 `INVALID_INPUT` 이다.
    """
    return ok(await service.create_chapter(session, project_id, body))


@router.get(
    "/{projectId}/chapters/{chapterId}",
    dependencies=[Viewer],
    summary="챕터를 조회한다",
    response_model=Envelope[ChapterResponse],
    responses=ChapterMissing,
)
async def get_chapter(
    project_id: ProjectId, chapter_id: ChapterId, session: Session
) -> dict[str, Any]:
    """프로젝트의 viewer 이상."""
    return ok(await service.get_chapter(session, project_id, chapter_id))


@router.patch(
    "/{projectId}/chapters/{chapterId}",
    dependencies=[Editor],
    summary="챕터를 수정한다",
    response_model=Envelope[ChapterResponse],
    responses=ChapterMissing,
)
async def update_chapter(
    project_id: ProjectId, chapter_id: ChapterId, body: ChapterUpdate, session: Session
) -> dict[str, Any]:
    """프로젝트의 editor 이상. 보낸 필드만 바뀐다. `chapter_no` 가 겹치면 `INVALID_INPUT` 이다."""
    return ok(await service.update_chapter(session, project_id, chapter_id, body))


@router.delete(
    "/{projectId}/chapters/{chapterId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
    summary="챕터를 삭제한다",
    responses=ChapterMissing,
)
async def delete_chapter(project_id: ProjectId, chapter_id: ChapterId, session: Session) -> None:
    """프로젝트의 editor 이상. 되돌릴 수 없다."""
    await service.delete_chapter(session, project_id, chapter_id)
