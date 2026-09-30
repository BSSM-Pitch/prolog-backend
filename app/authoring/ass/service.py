"""ASS (Ring 3) — 캐릭터 초안 편집 · 확정 · 확정 캐릭터.

이번 범위는 사용자가 직접 만드는 경로(`origin = user_added`)다. NLCD forward 수신과
AI 재추천은 없다 — ERD 가 "재추천은 draft_items 를 건드리지 않는다" 고 못박았다.

- 편집 이력은 **append-only** 다. 이 모듈에 이력을 고치거나 지우는 경로는 없다.
- 확정은 동기다. 실패는 에러 응답이고, 화면의 "다시 시도" 는 같은 요청을 다시 보내는 것이다.
- 초안을 바꾸는 모든 경로는 초안 행을 잠근다 — 확정이 항목을 읽는 도중 항목이 추가되지 않게.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.authoring.ass.models import (
    Character,
    CharacterAttribute,
    CharacterDraft,
    CharacterDraftItem,
    CharacterEditHistory,
)
from app.authoring.ass.schemas import (
    CATEGORY_OF,
    FIELD_OF,
    STATUS_IN,
    STATUS_OUT,
    AttributeResponse,
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

ACTION_OUT = {"create": "added", "update": "modified", "delete": "removed"}
Cursor = tuple[datetime, UUID] | None


# --- 조회 헬퍼 -------------------------------------------------------------


async def _draft(session: AsyncSession, project_id: UUID, draft_id: UUID) -> CharacterDraft:
    stmt = select(CharacterDraft).where(
        CharacterDraft.id == draft_id, CharacterDraft.project_id == project_id
    )
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise errors.DraftNotFound()
    return draft


async def _pending(session: AsyncSession, project_id: UUID, draft_id: UUID) -> CharacterDraft:
    """편집·확정·폐기 대상. 행을 잠그고, 이미 끝난 초안이면 409."""
    stmt = (
        select(CharacterDraft)
        .where(CharacterDraft.id == draft_id, CharacterDraft.project_id == project_id)
        .with_for_update()
    )
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise errors.DraftNotFound()
    if draft.status != "pending":
        raise errors.DraftAlreadyResolved(status=STATUS_OUT[draft.status])
    return draft


async def _item(session: AsyncSession, draft: CharacterDraft, item_id: UUID) -> CharacterDraftItem:
    stmt = select(CharacterDraftItem).where(
        CharacterDraftItem.id == item_id, CharacterDraftItem.draft_id == draft.id
    )
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise errors.ItemNotFound()
    return item


async def _character(
    session: AsyncSession, project_id: UUID, character_id: UUID, *, lock: bool = False
) -> Character:
    stmt = select(Character).where(
        Character.id == character_id,
        Character.project_id == project_id,
        Character.status == "active",
    )
    if lock:
        stmt = stmt.with_for_update()
    character = (await session.execute(stmt)).scalar_one_or_none()
    if character is None:
        raise errors.CharacterNotFound()
    return character


def _grouped(rows: Sequence[Any], build: Any) -> dict[str, list[Any]]:
    """명세 §2.1 의 모양 — 필드별 배열. 비어 있는 필드도 [] 로 내보낸다."""
    out: dict[str, list[Any]] = {field: [] for field in CATEGORY_OF}
    for row in rows:
        out[FIELD_OF[row.category]].append(build(row))
    return out


def _item_response(item: CharacterDraftItem) -> ItemResponse:
    return ItemResponse(
        item_id=item.id,
        field=FIELD_OF[item.category],
        value=item.value,
        evidence=item.evidence,
        origin=item.origin,  # type: ignore[arg-type]
    )


async def to_draft_responses(
    session: AsyncSession, drafts: Sequence[CharacterDraft]
) -> list[DraftResponse]:
    """목록용. 초안 수만큼 항목 쿼리를 날리지 않는다."""
    items: dict[UUID, list[CharacterDraftItem]] = {d.id: [] for d in drafts}
    if drafts:
        stmt = (
            select(CharacterDraftItem)
            .where(CharacterDraftItem.draft_id.in_(items))
            .order_by(CharacterDraftItem.created_at, CharacterDraftItem.id)
        )
        for item in (await session.execute(stmt)).scalars():
            items[item.draft_id].append(item)
    return [
        DraftResponse.model_validate(
            dict(
                draft_id=d.id,
                project_id=d.project_id,
                character_name=d.name,
                status=STATUS_OUT[d.status],
                source_job_id=d.source_job_id,
                confirmed_character_id=d.confirmed_character_id,
                confirmed_at=d.confirmed_at,
                created_at=d.created_at,
                updated_at=d.updated_at,
                **_grouped(items[d.id], _item_response),
            )
        )
        for d in drafts
    ]


async def _draft_response(session: AsyncSession, draft: CharacterDraft) -> DraftResponse:
    await session.flush()
    await session.refresh(draft)
    return (await to_draft_responses(session, [draft]))[0]


async def to_character_responses(
    session: AsyncSession, characters: Sequence[Character]
) -> list[CharacterResponse]:
    attrs: dict[UUID, list[CharacterAttribute]] = {c.id: [] for c in characters}
    if characters:
        stmt = (
            select(CharacterAttribute)
            .where(CharacterAttribute.character_id.in_(attrs))
            .order_by(CharacterAttribute.created_at, CharacterAttribute.id)
        )
        for attr in (await session.execute(stmt)).scalars():
            attrs[attr.character_id].append(attr)

    def build(a: CharacterAttribute) -> AttributeResponse:
        return AttributeResponse(
            attribute_id=a.id,
            field=FIELD_OF[a.category],
            value=a.value,
            evidence=a.evidence,
            origin=a.origin,  # type: ignore[arg-type]
        )

    return [
        CharacterResponse.model_validate(
            dict(
                character_id=c.id,
                project_id=c.project_id,
                name=c.name,
                created_from_draft_id=c.created_from_draft_id,
                confirmed_at=c.confirmed_at,
                updated_at=c.updated_at,
                **_grouped(attrs[c.id], build),
            )
        )
        for c in characters
    ]


async def _character_response(session: AsyncSession, character: Character) -> CharacterResponse:
    await session.flush()
    await session.refresh(character)
    return (await to_character_responses(session, [character]))[0]


def _log(session: AsyncSession, project_id: UUID, user_id: UUID, **fields: Any) -> None:
    """이력 한 줄을 붙인다. append-only — 이 테이블을 UPDATE/DELETE 하는 코드는 없다.

    `now()` 는 트랜잭션 시작 시각이라 확정처럼 한 요청에서 여러 줄을 쓰면 순서가 사라진다.
    """
    session.add(
        CharacterEditHistory(
            project_id=project_id,
            edited_by=user_id,
            created_at=func.clock_timestamp(),
            **fields,
        )
    )


def _page_desc(stmt: Any, model: Any, limit: int, cursor: Cursor) -> Any:
    stmt = stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(model.created_at, model.id) < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return stmt


# --- 초안 -----------------------------------------------------------------


async def create_draft(
    session: AsyncSession, project_id: UUID, body: DraftCreate, user_id: UUID
) -> DraftResponse:
    draft = CharacterDraft(project_id=project_id, name=body.character_name, created_by=user_id)
    session.add(draft)
    await session.flush()
    if body.character_name is not None:
        _log(
            session,
            project_id,
            user_id,
            draft_id=draft.id,
            phase="draft",
            action="create",
            field="character_name",
            after_value=body.character_name,
        )
    return await _draft_response(session, draft)


async def character_name(session: AsyncSession, project_id: UUID, character_id: UUID) -> str | None:
    """확정 캐릭터의 이름. 없으면 None.

    NLCD 의 `target_character_id` 확인용이다(조합 레이어가 부른다).
    """
    stmt = select(Character.name).where(
        Character.id == character_id,
        Character.project_id == project_id,
        Character.status == "active",
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_draft_from_extraction(
    session: AsyncSession,
    project_id: UUID,
    *,
    job_id: UUID,
    source_text: str,
    name: str | None,
    extracted: dict[str, list[dict[str, Any]]],
    user_id: UUID,
) -> DraftResponse:
    """NLCD forward(명세 §4.5)가 만드는 초안. 항목은 전부 `ai_extracted` 이고 근거를 갖는다.

    AI 가 넣은 항목은 사용자 편집이 아니므로 편집 이력에 남기지 않는다. 영향 관계의 `type` 은
    버린다 — 초안 항목은 `value` 하나다(DDL · 화면 23).
    """
    draft = CharacterDraft(
        project_id=project_id,
        source_job_id=job_id,
        source_text=source_text,
        name=name,
        created_by=user_id,
    )
    session.add(draft)
    await session.flush()
    for field, items in extracted.items():
        for item in items:
            session.add(
                CharacterDraftItem(
                    project_id=project_id,
                    draft_id=draft.id,
                    category=CATEGORY_OF[field],  # type: ignore[index]
                    value=item["value"],
                    evidence=item.get("evidence"),
                    origin="ai_extracted",
                    created_at=func.clock_timestamp(),  # 추출 순서를 지킨다
                )
            )
    return await _draft_response(session, draft)


async def list_drafts(
    session: AsyncSession,
    project_id: UUID,
    status: DraftStatus | None,
    limit: int,
    cursor: Cursor,
) -> list[CharacterDraft]:
    stmt = select(CharacterDraft).where(CharacterDraft.project_id == project_id)
    if status is not None:
        stmt = stmt.where(CharacterDraft.status == STATUS_IN[status])
    stmt = _page_desc(stmt, CharacterDraft, limit, cursor)
    return list((await session.execute(stmt)).scalars())


async def get_draft(session: AsyncSession, project_id: UUID, draft_id: UUID) -> DraftResponse:
    return await _draft_response(session, await _draft(session, project_id, draft_id))


async def update_draft(
    session: AsyncSession, project_id: UUID, draft_id: UUID, body: DraftUpdate, user_id: UUID
) -> DraftResponse:
    draft = await _pending(session, project_id, draft_id)
    if body.character_name != draft.name:
        _log(
            session,
            project_id,
            user_id,
            draft_id=draft.id,
            phase="draft",
            action="update" if draft.name else "create",
            field="character_name",
            before_value=draft.name,
            after_value=body.character_name,
        )
        draft.name = body.character_name
    return await _draft_response(session, draft)


async def add_item(
    session: AsyncSession, project_id: UUID, draft_id: UUID, body: ItemCreate, user_id: UUID
) -> ItemResponse:
    draft = await _pending(session, project_id, draft_id)
    # 이 경로로 들어오는 항목은 전부 사용자가 직접 넣은 것이다. 근거 문장은 AI 추출에만 있다.
    item = CharacterDraftItem(
        project_id=project_id,
        draft_id=draft.id,
        category=CATEGORY_OF[body.field],
        value=body.value,
        origin="user_added",
    )
    session.add(item)
    await session.flush()
    _log(
        session,
        project_id,
        user_id,
        draft_id=draft.id,
        item_id=item.id,
        phase="draft",
        action="create",
        field=body.field,
        after_value=body.value,
    )
    return _item_response(item)


async def update_item(
    session: AsyncSession,
    project_id: UUID,
    draft_id: UUID,
    item_id: UUID,
    body: ItemUpdate,
    user_id: UUID,
) -> ItemResponse:
    draft = await _pending(session, project_id, draft_id)
    item = await _item(session, draft, item_id)
    common = {"draft_id": draft.id, "item_id": item.id, "phase": "draft", "action": "update"}
    # 카테고리 변경과 값 변경은 따로 남긴다 — 항목 하나의 이력을 펼치면 둘 다 보인다.
    if body.field is not None and CATEGORY_OF[body.field] != item.category:
        _log(
            session,
            project_id,
            user_id,
            field="category",
            before_value=FIELD_OF[item.category],
            after_value=body.field,
            **common,
        )
        item.category = CATEGORY_OF[body.field]
    if body.value is not None and body.value != item.value:
        _log(
            session,
            project_id,
            user_id,
            field=FIELD_OF[item.category],
            before_value=item.value,
            after_value=body.value,
            **common,
        )
        item.value = body.value
    await session.flush()
    await session.refresh(item)
    return _item_response(item)


async def delete_item(
    session: AsyncSession, project_id: UUID, draft_id: UUID, item_id: UUID, user_id: UUID
) -> None:
    draft = await _pending(session, project_id, draft_id)
    item = await _item(session, draft, item_id)
    _log(
        session,
        project_id,
        user_id,
        draft_id=draft.id,
        item_id=item.id,
        phase="draft",
        action="delete",
        field=FIELD_OF[item.category],
        before_value=item.value,
    )
    await session.delete(item)
    await session.flush()


async def discard(session: AsyncSession, project_id: UUID, draft_id: UUID) -> DraftResponse:
    draft = await _pending(session, project_id, draft_id)
    draft.status = "discarded"
    return await _draft_response(session, draft)


# --- 확정 -----------------------------------------------------------------


async def _check_name(
    session: AsyncSession, project_id: UUID, name: str, *, exclude: UUID | None = None
) -> None:
    """같은 이름(대소문자 무시)의 확정 캐릭터가 있으면 409 + 가장 먼저 만든 후보.

    DB 제약이 없으므로(0005 — 화면 37 이 동명 캐릭터를 허용한다) 앱이 판정한다. 같은 이름을
    동시에 확정하는 두 요청이 둘 다 검사를 통과하지 않게 이름 단위 advisory lock 으로 줄 세운다
    (트랜잭션이 끝나면 풀린다).
    """
    key = func.hashtextextended(
        func.concat("character:", str(project_id), ":", func.lower(name)), 0
    )
    await session.execute(select(func.pg_advisory_xact_lock(key)))
    stmt = (
        select(Character.id)
        .where(
            Character.project_id == project_id,
            func.lower(Character.name) == func.lower(name),
            Character.status == "active",
        )
        .order_by(Character.created_at, Character.id)
        .limit(1)
    )
    if exclude is not None:
        stmt = stmt.where(Character.id != exclude)
    candidate = (await session.execute(stmt)).scalar_one_or_none()
    if candidate is not None:
        raise errors.DuplicateCharacterCandidate(candidate_character_id=str(candidate))


async def confirm(
    session: AsyncSession, project_id: UUID, draft_id: UUID, body: ConfirmRequest, user_id: UUID
) -> tuple[CharacterResponse, bool]:
    """(캐릭터, 새로 만들었는가). merge 면 False — 라우터가 200 으로 답한다.

    항목의 `evidence`·`origin` 은 그대로 승계한다(두 테이블이 같은 모양인 이유).
    이미 있는 값(같은 category · 대소문자 무시 같은 value)은 건너뛴다 — attributes 의 UNIQUE 다.

    - resolution 없음: 같은 이름의 확정 캐릭터가 있으면 409 + `candidate_character_id`.
    - `create_new`: 이름을 검사하지 않는다 — 화면 37 이 "같은 이름의 인물이 하나 더 생겨요" 라고
      알린 뒤 고른 선택지다.
    """
    draft = await _pending(session, project_id, draft_id)
    if not draft.name:
        raise errors.MissingRequiredField(
            "캐릭터 이름이 비어 있어 확정할 수 없습니다", field="character_name"
        )
    items_stmt = (
        select(CharacterDraftItem)
        .where(CharacterDraftItem.draft_id == draft.id)
        .order_by(CharacterDraftItem.created_at, CharacterDraftItem.id)
    )
    items = list((await session.execute(items_stmt)).scalars())

    if body.resolution == "merge":
        if body.merge_target_character_id is None:
            raise errors.InvalidInput(
                "merge 에는 merge_target_character_id 가 필요합니다",
                field="merge_target_character_id",
            )
        character = await _character(session, project_id, body.merge_target_character_id, lock=True)
        character.updated_at = func.now()
        created = False
    else:
        if body.resolution is None:
            await _check_name(session, project_id, draft.name)
        character = Character(
            project_id=project_id, name=draft.name, created_from_draft_id=draft.id
        )
        session.add(character)
        await session.flush()
        created = True

    trail = {"character_id": character.id, "draft_id": draft.id, "phase": "confirmed"}
    if created:
        _log(
            session,
            project_id,
            user_id,
            action="create",
            field="name",
            after_value=character.name,
            **trail,
        )
    if items:
        stmt = (
            insert(CharacterAttribute)
            .values(
                [
                    {
                        "project_id": project_id,
                        "character_id": character.id,
                        "category": i.category,
                        "value": i.value,
                        "evidence": i.evidence,
                        "origin": i.origin,
                        # 초안의 항목 순서를 지킨다(now() 는 한 트랜잭션 안에서 같다)
                        "created_at": func.clock_timestamp(),
                    }
                    for i in items
                ]
            )
            .on_conflict_do_nothing()
            .returning(CharacterAttribute.id, CharacterAttribute.category, CharacterAttribute.value)
        )
        for attr_id, category, value in (await session.execute(stmt)).all():
            _log(
                session,
                project_id,
                user_id,
                action="create",
                field=FIELD_OF[category],
                item_id=attr_id,
                after_value=value,
                **trail,
            )

    draft.status = "confirmed"
    draft.confirmed_character_id = character.id
    draft.confirmed_at = func.now()
    await session.flush()
    return await _character_response(session, character), created


# --- 확정 캐릭터 ------------------------------------------------------------


async def list_characters(
    session: AsyncSession, project_id: UUID, limit: int, cursor: Cursor
) -> list[Character]:
    stmt = select(Character).where(Character.project_id == project_id, Character.status == "active")
    return list((await session.execute(_page_desc(stmt, Character, limit, cursor))).scalars())


async def get_character(
    session: AsyncSession, project_id: UUID, character_id: UUID
) -> CharacterResponse:
    return await _character_response(session, await _character(session, project_id, character_id))


async def update_character(
    session: AsyncSession,
    project_id: UUID,
    character_id: UUID,
    body: CharacterUpdate,
    user_id: UUID,
) -> CharacterResponse:
    character = await _character(session, project_id, character_id, lock=True)
    if body.name != character.name:
        # 이름 변경에는 "그래도 만들기" 선택지가 없으므로 겹치면 409 다.
        await _check_name(session, project_id, body.name, exclude=character.id)
        before = character.name
        character.name = body.name
        _log(
            session,
            project_id,
            user_id,
            character_id=character.id,
            phase="confirmed",
            action="update",
            field="name",
            before_value=before,
            after_value=body.name,
        )
    return await _character_response(session, character)


async def delete_character(session: AsyncSession, project_id: UUID, character_id: UUID) -> None:
    """물리 삭제(아키텍처: 소프트 삭제 없음). 속성·확정 후 이력은 CASCADE 로 함께 사라진다.

    초안은 남는다(`confirmed_character_id` 가 SET NULL). 초안 단계 이력도 남는다.
    """
    await session.delete(await _character(session, project_id, character_id))
    await session.flush()


# --- 편집 이력 --------------------------------------------------------------


def to_history(rows: Sequence[CharacterEditHistory]) -> list[EditHistoryEntry]:
    return [
        EditHistoryEntry(
            history_id=h.id,
            action=ACTION_OUT[h.action],  # type: ignore[arg-type]
            field=h.field,
            value=h.before_value if h.action == "delete" else h.after_value,
            before_value=h.before_value,
            after_value=h.after_value,
            item_id=h.item_id,
            edited_by=h.edited_by,
            at=h.created_at,
        )
        for h in rows
    ]


async def _history(session: AsyncSession, where: list[Any], limit: int, cursor: Cursor) -> Any:
    """시간 순(오래된 것부터) — 명세 §4.11 예시 순서다."""
    H = CharacterEditHistory
    stmt = select(H).where(*where).order_by(H.created_at, H.id).limit(limit + 1)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(H.created_at, H.id) > tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def draft_history(
    session: AsyncSession,
    project_id: UUID,
    draft_id: UUID,
    item_id: UUID | None,
    limit: int,
    cursor: Cursor,
) -> list[CharacterEditHistory]:
    draft = await _draft(session, project_id, draft_id)
    where = [CharacterEditHistory.draft_id == draft.id, CharacterEditHistory.phase == "draft"]
    if item_id is not None:
        where.append(CharacterEditHistory.item_id == item_id)
    return await _history(session, where, limit, cursor)


async def character_history(
    session: AsyncSession, project_id: UUID, character_id: UUID, limit: int, cursor: Cursor
) -> list[CharacterEditHistory]:
    character = await _character(session, project_id, character_id)
    where = [CharacterEditHistory.character_id == character.id]
    return await _history(session, where, limit, cursor)
