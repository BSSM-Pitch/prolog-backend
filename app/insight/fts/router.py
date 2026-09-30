from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.core import errors
from app.core.deps import require_project_role
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, next_cursor
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.insight.fts import service
from app.insight.fts.schemas import (
    Advisory,
    ChapterBody,
    ChapterForeshadowing,
    ForeshadowingCreate,
    ForeshadowingPage,
    ForeshadowingResponse,
    ForeshadowingUpdate,
    LinkBody,
    PayoffBody,
    Status,
    TimelineTrack,
    UnresolvedResponse,
)

router = APIRouter(prefix="/projects", tags=["FTS"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
FId = Annotated[UUID, Path(alias="foreshadowingId")]
ChapterId = Annotated[UUID, Path(alias="chapterId")]
TargetId = Annotated[UUID, Path(alias="targetId")]
StatusQ = Annotated[Status | None, Query(alias="status")]
From = Annotated[int | None, Query(ge=1, description="설치~회수 범위가 이 챕터 이후와 겹치는 것")]
To = Annotated[int | None, Query(ge=1, description="설치~회수 범위가 이 챕터 이전과 겹치는 것")]
Current = Annotated[int, Query(ge=1, description="경과 챕터 수 계산 기준이 되는 현재 챕터")]
# ASSUMPTION: 명세가 역할을 정하지 않았다. MSU·ASS·REX 와 같이 읽기 viewer / 쓰기 editor.
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))

ALL = "/{projectId}/foreshadowings"
ONE = ALL + "/{foreshadowingId}"
Missing = raises(errors.ForeshadowingNotFound)


# --- 미회수 · 타임라인 · 챕터 역참조 (경로 순서: /unresolved 가 /{id} 보다 먼저다) -------


@router.get(
    ALL + "/unresolved",
    dependencies=[Viewer],
    summary="미회수 복선 목록",
    response_model=Envelope[UnresolvedResponse],
)
async def unresolved(
    project_id: ProjectId,
    session: Session,
    current_chapter: Current,
    sort: Literal["elapsed_desc", "setup_chapter_asc"] = "elapsed_desc",
) -> dict[str, Any]:
    """`status = unresolved` 이고 설치 챕터가 `current_chapter` 이하인 복선. 오래 경과한 것부터.

    `sort` 의 두 값은 같은 순서다(경과 = 현재 − 설치). 설치 챕터가 없는 `orphaned` 는 빠진다.
    """
    return ok(await service.unresolved(session, project_id, current_chapter))


@router.get(
    ALL + "/unresolved/advisories",
    dependencies=[Viewer],
    summary="미회수 안내 메시지",
    response_model=Envelope[list[Advisory]],
)
async def advisories(
    project_id: ProjectId, session: Session, current_chapter: Current
) -> dict[str, Any]:
    """결정론적 템플릿이다(AI 호출 없음).

    `priority`: 경과 10 미만 low · 10~25 medium · 25 초과 high.
    """
    return ok(await service.advisories(session, project_id, current_chapter))


@router.get(
    "/{projectId}/foreshadowing-timeline",
    dependencies=[Viewer],
    summary="복선 타임라인",
    response_model=Envelope[list[TimelineTrack]],
)
async def timeline(
    project_id: ProjectId,
    session: Session,
    status_: StatusQ = None,
    chapter_from: From = None,
    chapter_to: To = None,
) -> dict[str, Any]:
    """복선마다 트랙 하나. `markers` 는 챕터 순, `is_open` 은 회수되지 않은 트랙이다.

    시각화 한 화면 분량이라 페이지로 자르지 않는다.
    """
    return ok(await service.timeline(session, project_id, status_, chapter_from, chapter_to))


@router.get(
    "/{projectId}/chapters/{chapterId}/foreshadowings",
    dependencies=[Viewer],
    summary="챕터를 참조하는 복선 (삭제 전 확인용)",
    response_model=Envelope[list[ChapterForeshadowing]],
)
async def of_chapter(
    project_id: ProjectId, chapter_id: ChapterId, session: Session
) -> dict[str, Any]:
    """챕터를 지우기 전에 부른다. `role = setup` 이 있으면 삭제 후 그 복선은 `orphaned` 가 되고,
    사용자가 설치 챕터를 다시 지정해야 한다(자동 재지정하지 않는다)."""
    return ok(await service.of_chapter(session, project_id, chapter_id))


# --- 복선 -----------------------------------------------------------------


@router.get(
    ALL,
    dependencies=[Viewer],
    summary="복선 목록",
    response_model=ForeshadowingPage,
)
async def list_foreshadowings(
    project_id: ProjectId,
    session: Session,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    status_: StatusQ = None,
    chapter_from: From = None,
    chapter_to: To = None,
    linked_character_id: UUID | None = None,
    linked_event_id: UUID | None = None,
) -> dict[str, Any]:
    """먼저 만든 복선부터. 커서 페이지네이션이다.

    `meta.status_counts` 는 필터와 무관한 프로젝트 전체의 상태별 개수다(화면 04 복선 현황).
    """
    rows = await service.list_foreshadowings(
        session,
        project_id,
        limit,
        decode_cursor(cursor) if cursor else None,
        status=status_,
        chapter_from=chapter_from,
        chapter_to=chapter_to,
        linked_character_id=linked_character_id,
        linked_event_id=linked_event_id,
    )
    page, meta = next_cursor(rows, limit)
    meta["status_counts"] = (await service.status_counts(session, project_id)).model_dump()
    return ok(await service.to_responses(session, page), meta)


@router.post(
    ALL,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Editor],
    summary="복선을 만든다",
    response_model=Envelope[ForeshadowingResponse],
    responses=raises(errors.LinkTargetNotFound),
)
async def create(
    project_id: ProjectId, body: ForeshadowingCreate, session: Session
) -> dict[str, Any]:
    """설치 챕터는 필수다. 상태는 `unresolved` 로 시작한다.

    제목이 비슷한 복선이 있어도 만든다 — `meta.similar_candidates` 로 알려줄 뿐이다(명세 12항).
    """
    data, similar = await service.create(session, project_id, body)
    return ok(data, {"similar_candidates": [s.model_dump(mode="json") for s in similar]})


@router.get(
    ONE,
    dependencies=[Viewer],
    summary="복선을 조회한다",
    response_model=Envelope[ForeshadowingResponse],
    responses=Missing,
)
async def get(project_id: ProjectId, foreshadowing_id: FId, session: Session) -> dict[str, Any]:
    """`chapters` 에 설치·연결·회수 챕터의 id 와 번호가 있다."""
    return ok(await service.get(session, project_id, foreshadowing_id))


@router.patch(
    ONE,
    dependencies=[Editor],
    summary="복선을 수정한다",
    response_model=Envelope[ForeshadowingResponse],
    responses=raises(errors.ForeshadowingNotFound, errors.InvalidPayoffChapter),
)
async def update(
    project_id: ProjectId, foreshadowing_id: FId, body: ForeshadowingUpdate, session: Session
) -> dict[str, Any]:
    """보낸 필드만 바뀐다. `setup_chapter_id` 로 설치 챕터를 다시 지정하면 `orphaned` 가 풀린다.

    회수는 `PUT/DELETE .../payoff` 로만 바뀐다.
    """
    return ok(await service.update(session, project_id, foreshadowing_id, body))


@router.delete(
    ONE,
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
    summary="복선을 삭제한다",
    responses=Missing,
)
async def remove(project_id: ProjectId, foreshadowing_id: FId, session: Session) -> None:
    """되돌릴 수 없다."""
    await service.remove(session, project_id, foreshadowing_id)


@router.post(
    ONE + "/linked-chapters",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Editor],
    summary="연결 챕터를 추가한다",
    response_model=Envelope[ForeshadowingResponse],
    responses=Missing,
)
async def add_linked(
    project_id: ProjectId, foreshadowing_id: FId, body: ChapterBody, session: Session
) -> dict[str, Any]:
    """설치 챕터보다 앞선 챕터면 `INVALID_INPUT`. 같은 챕터를 다시 보내면 그대로다."""
    return ok(await service.add_linked(session, project_id, foreshadowing_id, body.chapter_id))


@router.delete(
    ONE + "/linked-chapters/{chapterId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
    summary="연결 챕터를 뺀다",
    responses=raises(errors.ForeshadowingNotFound, errors.LinkedChapterNotFound),
)
async def remove_linked(
    project_id: ProjectId, foreshadowing_id: FId, chapter_id: ChapterId, session: Session
) -> None:
    """연결 챕터가 아니면 `LINKED_CHAPTER_NOT_FOUND`."""
    await service.remove_linked(session, project_id, foreshadowing_id, chapter_id)


@router.put(
    ONE + "/payoff",
    dependencies=[Editor],
    summary="회수 챕터를 지정한다",
    response_model=Envelope[ForeshadowingResponse],
    responses=raises(errors.ForeshadowingNotFound, errors.InvalidPayoffChapter),
)
async def set_payoff(
    project_id: ProjectId, foreshadowing_id: FId, body: PayoffBody, session: Session
) -> dict[str, Any]:
    """`status` 가 `resolved` 가 된다(설치 챕터가 없으면 `orphaned` 그대로). 다시 부르면 교체한다.

    설치 챕터보다 앞서면 `INVALID_PAYOFF_CHAPTER` — `details` 에 두 챕터 번호가 온다.
    """
    return ok(
        await service.set_payoff(session, project_id, foreshadowing_id, body.payoff_chapter_id)
    )


@router.delete(
    ONE + "/payoff",
    dependencies=[Editor],
    summary="회수를 취소한다",
    response_model=Envelope[ForeshadowingResponse],
    responses=raises(errors.ForeshadowingNotFound, errors.PayoffNotSet),
)
async def clear_payoff(
    project_id: ProjectId, foreshadowing_id: FId, session: Session
) -> dict[str, Any]:
    """`unresolved` 로 되돌린다. 회수 챕터가 없으면 `PAYOFF_NOT_SET`."""
    return ok(await service.clear_payoff(session, project_id, foreshadowing_id))


@router.post(
    ONE + "/links",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Editor],
    summary="캐릭터를 연결한다",
    response_model=Envelope[ForeshadowingResponse],
    responses=raises(errors.ForeshadowingNotFound, errors.LinkTargetNotFound),
)
async def link(
    project_id: ProjectId, foreshadowing_id: FId, body: LinkBody, session: Session
) -> dict[str, Any]:
    """`target_type` 은 지금 `character` 만 된다(사건은 SCDS 이후). 이 프로젝트의 확정 캐릭터가
    아니면 `LINK_TARGET_NOT_FOUND`. 캐릭터가 삭제되면 연결만 풀리고 복선은 남는다."""
    return ok(await service.link(session, project_id, foreshadowing_id, body.target_id))


@router.delete(
    ONE + "/links/character/{targetId}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Editor],
    summary="캐릭터 연결을 푼다",
    responses=Missing,
)
async def unlink(
    project_id: ProjectId, foreshadowing_id: FId, target_id: TargetId, session: Session
) -> None:
    """연결이 없어도 204 다."""
    await service.unlink(session, project_id, foreshadowing_id, target_id)
