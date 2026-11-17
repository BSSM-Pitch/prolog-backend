"""Writer-confirmed promotion of review candidates, atomic and idempotent per candidate."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel
from sqlalchemy import select

from app.content.manuscripts.models import Chapter
from app.core import errors
from app.core.deps import require_project_role
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.insight.fts import service as fts
from app.insight.fts.schemas import ForeshadowingCreate
from app.insight.rcv import service as rcv
from app.insight.rcv.schemas import RelationshipCreate
from app.insight.scds import narrative
from app.jobs.models import Job

router = APIRouter(prefix="/projects", tags=["SCDS"])
ProjectId = Annotated[UUID, Path(alias="projectId")]
JobId = Annotated[UUID, Path(alias="checkId")]
Index = Annotated[int, Path(ge=0)]
ALL = "/{projectId}/conflict-checks/{checkId}/adoptions"


class Adoption(BaseModel):
    index: int
    kind: str
    target_id: UUID


class AdoptionInput(BaseModel):
    foreshadowing: ForeshadowingCreate | None = None
    payoff_chapter_id: UUID | None = None
    relationship: RelationshipCreate | None = None
    relationship_id: UUID | None = None


async def review(session: Session, project_id: UUID, check_id: UUID, lock: bool = False) -> Job:
    stmt = select(Job).where(
        Job.id == check_id,
        Job.project_id == project_id,
        Job.input["review_mode"].astext == "narrative-v1",
    )
    job = await session.scalar(stmt.with_for_update() if lock else stmt)
    if job is None:
        raise errors.ConflictCheckNotFound()
    return job


@router.get(
    ALL,
    dependencies=[Depends(require_project_role("viewer"))],
    summary="검토 후보에서 등록한 기록 목록",
    response_model=Envelope[list[Adoption]],
    responses=raises(errors.ConflictCheckNotFound),
)
async def adopted(project_id: ProjectId, check_id: JobId, session: Session) -> dict[str, Any]:
    """viewer 이상. 원고 검사별로 작가가 등록한 후보와 관리 기록 ID를 반환한다."""
    job = await review(session, project_id, check_id)
    return ok(list((job.result or {}).get("writer_adoptions", {}).values()))


@router.post(
    ALL + "/{index}",
    dependencies=[Depends(require_project_role("editor"))],
    summary="검토 후보를 작가가 확인한 관계·복선으로 등록",
    response_model=Envelope[Adoption],
    responses=raises(
        errors.ConflictCheckNotFound,
        errors.CharacterNotFound,
        errors.ChapterNotFound,
        errors.DuplicateRelationship,
        errors.DuplicateChapterRecord,
        errors.RelationshipNotFound,
        errors.InvalidPayoffChapter,
    ),
)
async def adopt(
    project_id: ProjectId, check_id: JobId, index: Index, body: AdoptionInput, session: Session
) -> dict[str, Any]:
    """editor 이상. 최신 검사만 등록 가능하며, 같은 후보의 재요청은 같은 기록을 반환한다.

    관계는 인물과 상태를, 복선은 제목과 설치 장을 확인해 보낸다. 기존 관계 기록을
    덮어쓰지 않는다. 복선 생성과 회수 지정은 하나의 트랜잭션이다.
    """
    job = await review(session, project_id, check_id, lock=True)
    data = job.result or {}
    saved = dict(data.get("writer_adoptions", {}))
    if str(index) in saved:
        return ok(saved[str(index)])
    tracks = data.get("tracks", [])
    if job.status != "completed" or index >= len(tracks):
        raise errors.InvalidInput("완료된 검토의 후보를 선택해 주세요.")
    if await narrative.is_stale(session, job):
        raise errors.InvalidInput("원고가 변경되었습니다. 다시 검사한 뒤 등록해 주세요.")
    track = tracks[index]
    expected = "foreshadowing" if body.foreshadowing else "relationship"
    if track["kind"] != expected or bool(body.foreshadowing) == bool(body.relationship):
        raise errors.InvalidInput("후보 종류에 맞는 기록을 선택해 주세요.")
    chapter_ids = (
        [body.foreshadowing.setup_chapter_id]
        if body.foreshadowing
        else [body.relationship.initial_history.chapter_id]
        if body.relationship
        else []
    )
    if body.payoff_chapter_id:
        if not body.foreshadowing:
            raise errors.InvalidInput("회수 장은 복선에만 지정할 수 있어요.")
        chapter_ids.append(body.payoff_chapter_id)
    valid = set(
        await session.scalars(
            select(Chapter.id).where(
                Chapter.project_id == project_id,
                Chapter.manuscript_id == UUID(job.input["manuscript_id"]),
                Chapter.id.in_(chapter_ids),
            )
        )
    )
    if set(chapter_ids) != valid:
        raise errors.ChapterNotFound()
    if body.foreshadowing:
        row, _ = await fts.create(session, project_id, body.foreshadowing)
        target_id = row.foreshadowing_id
        if body.payoff_chapter_id:
            await fts.set_payoff(session, project_id, target_id, body.payoff_chapter_id)
    else:
        assert body.relationship is not None
        if body.relationship_id:
            # Existing pair: add a chapter record; never overwrite an existing chapter silently.
            existing = await rcv.get(session, project_id, body.relationship_id)
            pair = {existing.source_character_id, existing.target_character_id}
            if pair != {
                body.relationship.source_character_id,
                body.relationship.target_character_id,
            }:
                raise errors.InvalidInput("선택한 관계와 인물이 일치하지 않아요.")
            await rcv.record(
                session,
                project_id,
                body.relationship_id,
                body.relationship.initial_history,
                overwrite=False,
            )
            target_id = body.relationship_id
        else:
            relationship = await rcv.create(session, project_id, body.relationship)
            target_id = relationship.relationship_id
    entry = {"index": index, "kind": expected, "target_id": str(target_id)}
    saved[str(index)] = entry
    job.result = {**data, "writer_adoptions": saved}
    await session.flush()
    return ok(entry)
