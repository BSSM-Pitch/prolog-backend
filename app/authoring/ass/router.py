from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.authoring.ass import service
from app.authoring.ass.schemas import (
    CharacterResponse,
    CharacterUpdate,
    ConfirmRequest,
    DraftCreate,
    DraftResponse,
    DraftStatus,
    DraftUpdate,
    EditHistoryEntry,
    ItemCreate,
    ItemResponse,
    ItemUpdate,
)
from app.core import errors
from app.core.deps import ProjectContext, require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, Page, ok, raises
from app.db.session import Session

router = APIRouter(prefix="/projects", tags=["ASS"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
DraftId = Annotated[UUID, Path(alias="draftId")]
ItemId = Annotated[UUID, Path(alias="itemId")]
CharacterId = Annotated[UUID, Path(alias="characterId")]
# ASSUMPTION: 명세가 역할을 정하지 않았다. MSU 와 같이 읽기 viewer / 쓰기 editor.
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
EditorCtx = Annotated[ProjectContext, Depends(require_project_role("editor"))]

DRAFTS = "/{projectId}/character-drafts"
DRAFT = DRAFTS + "/{draftId}"
ITEM = DRAFT + "/items/{itemId}"
CHARACTERS = "/{projectId}/characters"
CHARACTER = CHARACTERS + "/{characterId}"

Resolved = (errors.DraftNotFound, errors.DraftAlreadyResolved)


def _cursor(cursor: str | None) -> Any:
    return decode_cursor(cursor) if cursor else None


# --- 초안 -----------------------------------------------------------------


@router.post(
    DRAFTS,
    status_code=status.HTTP_201_CREATED,
    summary="빈 초안을 만든다",
    response_model=Envelope[DraftResponse],
)
async def create_draft(
    project_id: ProjectId, body: DraftCreate, session: Session, ctx: EditorCtx
) -> dict[str, Any]:
    """프로젝트의 editor 이상. 사용자가 직접 설계하는 경로다 — `source_job_id` 는 null.

    항목은 `POST .../items` 로 채운다. `character_name` 은 비워도 되지만 확정 때는 필수다.
    """
    return ok(await service.create_draft(session, project_id, body, ctx.user.id))


@router.get(
    DRAFTS,
    dependencies=[Viewer],
    summary="초안 목록",
    response_model=Page[DraftResponse],
)
async def list_drafts(
    project_id: ProjectId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    status_: Annotated[DraftStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    """최근에 만든 초안부터. 커서 페이지네이션이다. 항목을 전부 포함한다."""
    rows = await service.list_drafts(session, project_id, status_, limit, _cursor(cursor))
    page, meta = next_cursor(rows, limit)
    return ok(await service.to_draft_responses(session, page), meta)


@router.get(
    DRAFT,
    dependencies=[Viewer],
    summary="초안을 조회한다",
    response_model=Envelope[DraftResponse],
    responses=raises(errors.DraftNotFound),
)
async def get_draft(project_id: ProjectId, draft_id: DraftId, session: Session) -> dict[str, Any]:
    """항목은 필드별로 묶여 있고, 각 필드 안에서는 추가한 순서다."""
    return ok(await service.get_draft(session, project_id, draft_id))


@router.patch(
    DRAFT,
    summary="초안의 캐릭터 이름을 고친다",
    response_model=Envelope[DraftResponse],
    responses=raises(*Resolved),
)
async def update_draft(
    project_id: ProjectId, draft_id: DraftId, body: DraftUpdate, session: Session, ctx: EditorCtx
) -> dict[str, Any]:
    """편집 이력에 `field = character_name` 으로 남는다. 끝난 초안이면 `DRAFT_ALREADY_RESOLVED`."""
    return ok(await service.update_draft(session, project_id, draft_id, body, ctx.user.id))


@router.post(
    DRAFT + "/items",
    status_code=status.HTTP_201_CREATED,
    summary="초안에 항목을 추가한다",
    response_model=Envelope[ItemResponse],
    responses=raises(*Resolved),
)
async def add_item(
    project_id: ProjectId, draft_id: DraftId, body: ItemCreate, session: Session, ctx: EditorCtx
) -> dict[str, Any]:
    """`origin` 은 항상 `user_added`, `evidence` 는 null 이다."""
    return ok(await service.add_item(session, project_id, draft_id, body, ctx.user.id))


@router.patch(
    ITEM,
    summary="초안 항목을 고친다",
    response_model=Envelope[ItemResponse],
    responses=raises(*Resolved, errors.ItemNotFound),
)
async def update_item(
    project_id: ProjectId,
    draft_id: DraftId,
    item_id: ItemId,
    body: ItemUpdate,
    session: Session,
    ctx: EditorCtx,
) -> dict[str, Any]:
    """보낸 것만 바뀐다. `field`(카테고리)와 `value` 변경은 이력에 따로 남는다.

    `origin`·`evidence` 는 바뀌지 않는다 — AI 가 뽑은 항목을 고쳐도 근거 문장은 원문 그대로다.
    """
    return ok(await service.update_item(session, project_id, draft_id, item_id, body, ctx.user.id))


@router.delete(
    ITEM,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="초안 항목을 삭제한다",
    responses=raises(*Resolved, errors.ItemNotFound),
)
async def delete_item(
    project_id: ProjectId, draft_id: DraftId, item_id: ItemId, session: Session, ctx: EditorCtx
) -> None:
    """이력에는 지운 값이 남는다."""
    await service.delete_item(session, project_id, draft_id, item_id, ctx.user.id)


@router.post(
    DRAFT + "/confirm",
    status_code=status.HTTP_201_CREATED,
    summary="초안을 확정한다",
    response_model=Envelope[CharacterResponse],
    responses={
        200: {
            "model": Envelope[CharacterResponse],
            "description": "`merge` — 기존 캐릭터에 합쳤다",
        },
        **raises(
            *Resolved,
            errors.MissingRequiredField,
            errors.DuplicateCharacterCandidate,
            errors.CharacterNotFound,
        ),
    },
)
async def confirm(
    project_id: ProjectId,
    draft_id: DraftId,
    body: ConfirmRequest,
    session: Session,
    ctx: EditorCtx,
    response: Response,
) -> dict[str, Any]:
    """동기 처리다. 실패하면 편집 내용은 그대로 남고, 같은 요청을 다시 보내면 된다.

    - 본문 없음(`{}`) 또는 `create_new`: 새 캐릭터 → **201**. 같은 이름의 캐릭터가 있으면
      `DUPLICATE_CHARACTER_CANDIDATE`(409) 이고 `details.candidate_character_id` 가 온다.
      `create_new` 도 이름이 겹치면 409 다 — 초안 이름을 바꾼 뒤 다시 확정한다.
    - `merge` + `merge_target_character_id`: 기존 캐릭터에 항목을 합친다 → **200**.
      이미 있는 값은 건너뛴다. 대상이 없으면 `CHARACTER_NOT_FOUND`.
    - 이름이 비어 있으면 `MISSING_REQUIRED_FIELD`(`details.field = character_name`).

    항목의 `evidence`·`origin` 은 캐릭터 속성에 그대로 승계된다.
    """
    character, created = await service.confirm(session, project_id, draft_id, body, ctx.user.id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return ok(character)


@router.post(
    DRAFT + "/discard",
    summary="초안을 폐기한다",
    response_model=Envelope[DraftResponse],
    responses=raises(*Resolved),
)
async def discard(
    project_id: ProjectId, draft_id: DraftId, session: Session, ctx: EditorCtx
) -> dict[str, Any]:
    """화면을 그냥 떠나면 부르지 않는다 — 초안은 `pending_review` 로 남는다."""
    return ok(await service.discard(session, project_id, draft_id))


@router.get(
    DRAFT + "/edit-history",
    dependencies=[Viewer],
    summary="초안 편집 이력 (확정 전)",
    response_model=Page[EditHistoryEntry],
    responses=raises(errors.DraftNotFound),
)
async def draft_history(
    project_id: ProjectId,
    draft_id: DraftId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    item_id: Annotated[UUID | None, Query(description="이 항목의 이력만 본다")] = None,
) -> dict[str, Any]:
    """오래된 것부터. 화면의 "편집 이력 N건 펼치기" 는 `item_id` 를 붙여 부른다."""
    rows = await service.draft_history(
        session, project_id, draft_id, item_id, limit, _cursor(cursor)
    )
    page, meta = next_cursor(rows, limit)
    return ok(service.to_history(page), meta)


# --- 확정 캐릭터 ------------------------------------------------------------


@router.get(
    CHARACTERS,
    dependencies=[Viewer],
    summary="확정된 캐릭터 목록",
    response_model=Page[CharacterResponse],
)
async def list_characters(
    project_id: ProjectId, session: Session, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> dict[str, Any]:
    """최근에 확정한 것부터. 커서 페이지네이션이다."""
    rows = await service.list_characters(session, project_id, limit, _cursor(cursor))
    page, meta = next_cursor(rows, limit)
    return ok(await service.to_character_responses(session, page), meta)


@router.get(
    CHARACTER,
    dependencies=[Viewer],
    summary="확정된 캐릭터를 조회한다",
    response_model=Envelope[CharacterResponse],
    responses=raises(errors.CharacterNotFound),
)
async def get_character(
    project_id: ProjectId, character_id: CharacterId, session: Session
) -> dict[str, Any]:
    """속성마다 `origin`·`evidence` 가 있다 — AI 가 뽑은 설정인지 확정 후에도 보인다."""
    return ok(await service.get_character(session, project_id, character_id))


@router.patch(
    CHARACTER,
    summary="캐릭터 이름을 고친다",
    response_model=Envelope[CharacterResponse],
    responses=raises(errors.CharacterNotFound, errors.DuplicateCharacterCandidate),
)
async def update_character(
    project_id: ProjectId,
    character_id: CharacterId,
    body: CharacterUpdate,
    session: Session,
    ctx: EditorCtx,
) -> dict[str, Any]:
    """프로젝트의 editor 이상. 같은 이름의 캐릭터가 있으면 `DUPLICATE_CHARACTER_CANDIDATE`."""
    return ok(await service.update_character(session, project_id, character_id, body, ctx.user.id))


@router.delete(
    CHARACTER,
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
    summary="캐릭터를 삭제한다",
    responses=raises(errors.CharacterNotFound),
)
async def delete_character(
    project_id: ProjectId, character_id: CharacterId, session: Session
) -> None:
    """되돌릴 수 없다. 속성과 확정 후 이력이 함께 지워진다. 원본 초안은 남는다."""
    await service.delete_character(session, project_id, character_id)


@router.get(
    CHARACTER + "/edit-history",
    dependencies=[Viewer],
    summary="캐릭터 편집 이력 (확정 후)",
    response_model=Page[EditHistoryEntry],
    responses=raises(errors.CharacterNotFound),
)
async def character_history(
    project_id: ProjectId,
    character_id: CharacterId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """확정 시 승계된 속성과 이후 이름 변경. 오래된 것부터."""
    rows = await service.character_history(
        session, project_id, character_id, limit, _cursor(cursor)
    )
    page, meta = next_cursor(rows, limit)
    return ok(service.to_history(page), meta)
