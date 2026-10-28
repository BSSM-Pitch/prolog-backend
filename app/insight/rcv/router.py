from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from app.core import errors
from app.core.deps import require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, Page, ok, raises
from app.db.session import Session
from app.insight.rcv import service
from app.insight.rcv.schemas import (
    RelationshipCreate,
    RelationshipEntryCreate,
    RelationshipHistoryEntry,
    RelationshipMindmap,
    RelationshipResponse,
    RelationshipSnapshot,
)

router = APIRouter(prefix="/projects", tags=["RCV"])
ProjectId = Annotated[UUID, Path(alias="projectId")]
RelationshipId = Annotated[UUID, Path(alias="relationshipId")]
ChapterId = Annotated[UUID, Path(alias="chapterId")]
Chapter = Annotated[int, Query(ge=1)]
# ASSUMPTION: 다른 저작 기능과 동일하게 읽기 viewer / 쓰기 editor 권한.
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
ALL = "/{projectId}/relationships"
ONE = ALL + "/{relationshipId}"


@router.get(
    ALL,
    dependencies=[Viewer],
    response_model=Page[RelationshipResponse],
    responses=raises(errors.ManuscriptNotFound),
    summary="관계 목록과 원고별 이력",
)
async def list_relationships(
    project_id: ProjectId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    manuscript_id: UUID | None = None,
    character_id: UUID | None = None,
) -> dict[str, Any]:
    """생성 순 커서 목록. manuscript_id가 있으면 해당 원고의 이력만 포함한다."""
    if manuscript_id:
        await service.manuscript(session, project_id, manuscript_id)
    rows = await service.list_rows(
        session, project_id, limit, decode_cursor(cursor) if cursor else None, character_id
    )
    page, meta = next_cursor(rows, limit)
    return ok(await service.responses(session, page, manuscript_id), meta)


@router.post(
    ALL,
    dependencies=[Editor],
    status_code=201,
    response_model=Envelope[RelationshipResponse],
    responses=raises(
        errors.CharacterNotFound,
        errors.ChapterNotFound,
        errors.DuplicateRelationship,
        errors.DuplicateChapterRecord,
    ),
    summary="관계와 첫 장의 상태 기록",
)
async def create(
    project_id: ProjectId, body: RelationshipCreate, session: Session
) -> dict[str, Any]:
    """chapter_id로 원고를 구별한다. 다른 원고의 첫 기록은 기존 인물 쌍에 추가한다.

    event_title은 사용자가 입력하는 메모이며 사건 리소스 연결은 아니다.
    """
    return ok(await service.create(session, project_id, body))


@router.get(
    ONE,
    dependencies=[Viewer],
    response_model=Envelope[RelationshipResponse],
    responses=raises(errors.RelationshipNotFound, errors.ManuscriptNotFound),
    summary="관계 상세",
)
async def get(
    project_id: ProjectId,
    relationship_id: RelationshipId,
    session: Session,
    manuscript_id: UUID | None = None,
) -> dict[str, Any]:
    """관계와 장 순서로 정렬한 이력. 원고 필터가 없으면 모든 원고의 이력을 반환한다."""
    if manuscript_id:
        await service.manuscript(session, project_id, manuscript_id)
    row = await service.get(session, project_id, relationship_id)
    return ok((await service.responses(session, [row], manuscript_id))[0])


@router.delete(
    ONE,
    dependencies=[Editor],
    status_code=204,
    responses=raises(errors.RelationshipNotFound),
    summary="관계와 전체 이력 삭제",
)
async def delete(project_id: ProjectId, relationship_id: RelationshipId, session: Session) -> None:
    """이 인물 쌍의 모든 원고 이력을 함께 삭제한다. editor 이상만 가능하다."""
    await service.delete(session, project_id, relationship_id)


@router.post(
    ONE + "/history",
    dependencies=[Editor],
    status_code=201,
    response_model=Envelope[RelationshipHistoryEntry],
    responses=raises(
        errors.RelationshipNotFound, errors.ChapterNotFound, errors.DuplicateChapterRecord
    ),
    summary="장별 관계 상태 기록",
)
async def record(
    project_id: ProjectId,
    relationship_id: RelationshipId,
    body: RelationshipEntryCreate,
    session: Session,
    overwrite: bool = False,
) -> dict[str, Any]:
    """같은 chapter_id가 이미 있으면 409. overwrite=true일 때만 기존 기록을 변경한다."""
    return ok(await service.record(session, project_id, relationship_id, body, overwrite))


@router.delete(
    ONE + "/history/{chapterId}",
    dependencies=[Editor],
    status_code=204,
    responses=raises(errors.RelationshipNotFound, errors.RelationshipHistoryNotFound),
    summary="장별 관계 기록 삭제",
)
async def delete_record(
    project_id: ProjectId,
    relationship_id: RelationshipId,
    chapter_id: ChapterId,
    session: Session,
) -> None:
    """특정 장 ID의 기록만 삭제한다. 다른 원고의 같은 번호 기록은 유지한다."""
    await service.delete_record(session, project_id, relationship_id, chapter_id)


@router.get(
    ONE + "/snapshot",
    dependencies=[Viewer],
    response_model=Envelope[RelationshipSnapshot],
    responses=raises(errors.RelationshipNotFound, errors.ManuscriptNotFound),
    summary="원고의 특정 장 시점 관계",
)
async def snapshot(
    project_id: ProjectId,
    relationship_id: RelationshipId,
    session: Session,
    manuscript_id: UUID,
    chapter: Chapter,
) -> dict[str, Any]:
    """해당 원고에서 요청 장 이하의 마지막 상태. 이전 기록이 없으면 상태는 null이다."""
    await service.manuscript(session, project_id, manuscript_id)
    row = await service.get(session, project_id, relationship_id)
    response = (await service.responses(session, [row], manuscript_id))[0]
    return ok(service.snapshot(response, chapter))


@router.get(
    "/{projectId}/relationship-mindmap",
    dependencies=[Viewer],
    response_model=Envelope[RelationshipMindmap],
    responses=raises(errors.ManuscriptNotFound),
    summary="원고의 특정 장 관계 그래프",
)
async def mindmap(
    project_id: ProjectId,
    session: Session,
    manuscript_id: UUID,
    chapter: Chapter,
    character_id: UUID | None = None,
) -> dict[str, Any]:
    """원고·시점에 존재하는 관계만 반환한다. character_id는 직접 연결된 관계 필터다."""
    return ok(await service.mindmap(session, project_id, manuscript_id, chapter, character_id))
