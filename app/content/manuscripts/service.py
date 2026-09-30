"""MSU (Ring 2).

**DDL 이 정본이다** (CLAUDE.md §7). 명세와 다음이 다르고, 확정 결정은 DDL 쪽이다:
`source_type` 은 `editor|upload`(명세 `file` 아님), 파일은 `file_key`(URL 저장 금지),
`status` 는 `draft|processing|ready|failed`.

실패 사유 컬럼을 두지 않는다. 추출 실패는 `extraction_job_id` 가 가리키는 잡에 남는다 —
같은 상태를 두 곳에 두지 않는다.
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts import repository as repo
from app.content.manuscripts.extraction import SUPPORTED_FORMATS
from app.content.manuscripts.models import Chapter, Manuscript
from app.content.manuscripts.schemas import (
    ChapterCreate,
    ChapterResponse,
    ChapterUpdate,
    ManuscriptCreate,
    ManuscriptResponse,
    ManuscriptStatus,
    ManuscriptUpdate,
    UploadRequest,
    UploadResponse,
)
from app.content.manuscripts.storage import Storage, content_type_of, object_key
from app.core import errors
from app.events.outbox import emit
from app.jobs import service as jobs

CHAPTER_NO_UQ = "chapters_manuscript_no_uq"
EXTRACTION_JOB_TYPE = "manuscript_extraction"


async def manuscript_counts(session: AsyncSession, project_ids: Sequence[UUID]) -> dict[UUID, int]:
    """PRJ 명세 §2.1 의 `manuscript_count`.

    `content` 는 Ring 2 라서 `platform_.projects` 가 직접 셀 수 없다(규칙 1).
    조합 레이어가 이 함수를 호출해 프로젝트 응답에 합친다.
    """
    return await repo.count_by_projects(session, project_ids)


def _response(manuscript: Manuscript, chapter_count: int) -> ManuscriptResponse:
    return ManuscriptResponse.model_validate(manuscript, from_attributes=True).model_copy(
        update={"chapter_count": chapter_count}
    )


async def _one(session: AsyncSession, manuscript: Manuscript) -> ManuscriptResponse:
    counts = await repo.chapter_counts(session, [manuscript.id])
    return _response(manuscript, counts.get(manuscript.id, 0))


async def to_responses(
    session: AsyncSession, rows: Sequence[Manuscript]
) -> list[ManuscriptResponse]:
    """목록용. 원고 수만큼 집계 쿼리를 날리지 않는다."""
    counts = await repo.chapter_counts(session, [m.id for m in rows])
    return [_response(m, counts.get(m.id, 0)) for m in rows]


async def _get(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, *, lock: bool = False
) -> Manuscript:
    manuscript = await repo.get_manuscript(session, project_id, manuscript_id, lock=lock)
    if manuscript is None:
        raise errors.ManuscriptNotFound()
    return manuscript


async def create(
    session: AsyncSession, project_id: UUID, body: ManuscriptCreate
) -> ManuscriptResponse:
    """에디터 원고는 빈 본문으로 즉시 `ready`, 업로드 원고는 파일이 오기 전까지 `draft`."""
    editor = body.source_type == "editor"
    manuscript = await repo.add_manuscript(
        session,
        Manuscript(
            project_id=project_id,
            title=body.title,
            source_type=body.source_type,
            content="" if editor else None,
            status="ready" if editor else "draft",
        ),
    )
    return await _one(session, manuscript)


async def get(session: AsyncSession, project_id: UUID, manuscript_id: UUID) -> ManuscriptResponse:
    return await _one(session, await _get(session, project_id, manuscript_id))


async def list_manuscripts(
    session: AsyncSession, project_id: UUID, limit: int, cursor: tuple[datetime, UUID] | None
) -> list[Manuscript]:
    return await repo.list_manuscripts(session, project_id, limit, cursor)


async def update(
    session: AsyncSession, project_id: UUID, manuscript_id: UUID, body: ManuscriptUpdate
) -> ManuscriptResponse:
    manuscript = await _get(session, project_id, manuscript_id)
    if body.source_type is not None and body.source_type != manuscript.source_type:
        raise errors.SourceTypeImmutable()
    if body.title is not None:
        manuscript.title = body.title
    if body.content is not None:
        manuscript.content = body.content
    await session.flush()
    return await _one(session, manuscript)


def _chapter_deleted(session: AsyncSession, chapter: Chapter) -> None:
    """챕터를 참조하는 다른 모듈(FTS)은 이 이벤트로 정리한다.

    여기서 그들을 부르지 않는다(규칙 1 — Ring 2 는 Ring 4 를 모른다).
    """
    emit(
        session,
        aggregate_type="chapter",
        aggregate_id=chapter.id,
        event_type="chapter.deleted",
        payload={
            "project_id": str(chapter.project_id),
            "manuscript_id": str(chapter.manuscript_id),
            "chapter_id": str(chapter.id),
            "chapter_no": chapter.chapter_no,
        },
    )


async def delete(session: AsyncSession, project_id: UUID, manuscript_id: UUID) -> None:
    """원고를 지우면 챕터도 CASCADE 로 지워진다 — 챕터마다 삭제 이벤트를 남긴다."""
    manuscript = await _get(session, project_id, manuscript_id)
    for chapter in await repo.chapters_of(session, manuscript.id):
        _chapter_deleted(session, chapter)
    await session.delete(manuscript)
    await session.flush()


async def request_upload(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    body: UploadRequest,
    storage: Storage,
    user_id: UUID,
) -> UploadResponse:
    """presigned URL 을 발급하고 추출 잡을 건다. 파일 바이트는 서버를 지나지 않는다."""
    manuscript = await _get(session, project_id, manuscript_id)
    if manuscript.source_type != "upload":
        raise errors.InvalidInput("업로드 원고가 아닙니다")
    if body.file_format not in SUPPORTED_FORMATS:
        raise errors.UnsupportedFileFormat(supported=list(SUPPORTED_FORMATS))

    key = object_key(project_id, manuscript_id, body.file_format)
    presigned = storage.presigned_put(key, content_type_of(body.file_format))

    # 잡은 여기서 만들지 않는다. 파일이 아직 없으므로 큐에 넣으면 빈 대상을 가리킨다.
    manuscript.file_key = key
    await session.flush()
    return UploadResponse(
        manuscript_id=manuscript.id,
        file_key=key,
        upload_url=presigned.url,
        expires_in=presigned.expires_in,
        status=cast(ManuscriptStatus, manuscript.status),  # 아직 draft 다
    )


async def complete_upload(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    storage: Storage,
    user_id: UUID,
) -> ManuscriptResponse:
    """클라이언트가 S3 PUT 을 마친 뒤 부른다. 객체를 확인하고 추출 잡을 건다.

    S3 이벤트 알림이 아니라 콜백인 이유는 로컬에 S3 가 없어 재현이 어렵기 때문이다.
    콜백이 유실되면 `sweep_lost_uploads` 가 대신 부른다 — 행을 잠그므로 둘이 겹쳐도 잡은 하나다.
    """
    manuscript = await _get(session, project_id, manuscript_id, lock=True)
    if manuscript.source_type != "upload":
        raise errors.InvalidInput("업로드 원고가 아닙니다")
    if manuscript.file_key is None:
        raise errors.InvalidInput("업로드 URL 을 먼저 발급받아야 합니다")

    # ASSUMPTION: 두 번 불러도 잡이 두 개 생기지 않게 현재 상태를 그대로 돌려준다.
    if manuscript.extraction_job_id is not None:
        return await _one(session, manuscript)

    if not storage.exists(manuscript.file_key):
        # ASSUMPTION: 명세에 "업로드된 파일 없음" 코드가 없어 공통 코드로 답한다. 제안 목록에 있다.
        raise errors.InvalidInput("업로드된 파일을 찾을 수 없습니다", field="file_key")

    await _start_extraction(session, manuscript, user_id)
    return await _one(session, manuscript)


async def _start_extraction(
    session: AsyncSession, manuscript: Manuscript, user_id: UUID | None
) -> None:
    """잡을 만들고 원고를 processing 으로. 잡 알림은 같은 트랜잭션의 outbox 로 간다(§8)."""
    file_key = str(manuscript.file_key)
    job = await jobs.create(
        session,
        project_id=manuscript.project_id,
        job_type=EXTRACTION_JOB_TYPE,
        target_type="manuscript",
        target_id=manuscript.id,
        queue="io",
        input={"file_key": file_key, "file_format": file_key.rsplit(".", 1)[-1]},
        created_by=user_id,
    )
    manuscript.status = "processing"
    manuscript.extraction_job_id = job.id
    jobs.announce(session, job, manuscript_id=str(manuscript.id))
    await session.flush()


async def sweep_lost_uploads(
    session: AsyncSession, storage: Storage, older_than: timedelta, limit: int = 50
) -> list[UUID]:
    """업로드 콜백 유실 회수 (CLAUDE.md §12). 추출을 시작한 원고 id 를 돌려준다.

    대상은 **잡이 아예 없는** 원고다 — 좀비 회수(`running` 잡)와 겹치지 않는다.
    - S3 에 객체가 있다: 업로드는 끝났는데 콜백만 없다 → 콜백과 같은 경로로 추출을 건다.
    - 객체가 없다: 아직 안 올린 것이다. 정상적인 draft 이므로 **건드리지 않는다.**
      실패로 표시할 사유를 담을 잡도 없다.
    """
    started: list[UUID] = []
    before = datetime.now(UTC) - older_than
    for manuscript in await repo.lost_uploads(session, before, limit):
        if await asyncio.to_thread(storage.exists, str(manuscript.file_key)):
            await _start_extraction(session, manuscript, user_id=None)
            started.append(manuscript.id)
    return started


# --- 챕터 -----------------------------------------------------------------
# 경로는 `/projects/{projectId}/chapters/{chapterId}` 다 — SCDS 가 그렇게 참조하고
# `chapters.project_id NOT NULL` + `(project_id, chapter_no)` 인덱스가 이를 지원한다.
# 챕터는 원고보다 오래 산다: 원고를 갈아끼워도 3화는 같은 3화다.


def _chapter(row: Chapter) -> ChapterResponse:
    return ChapterResponse.model_validate(row, from_attributes=True)


async def create_chapter(
    session: AsyncSession, project_id: UUID, body: ChapterCreate
) -> ChapterResponse:
    # 남의 프로젝트 원고에 챕터를 달 수 없다.
    await _get(session, project_id, body.manuscript_id)
    try:
        row = await repo.add_chapter(
            session,
            Chapter(
                project_id=project_id,
                manuscript_id=body.manuscript_id,
                chapter_no=body.chapter_no,
                title=body.title,
                content=body.content,
            ),
        )
    except IntegrityError as exc:
        if errors.constraint_name(exc) == CHAPTER_NO_UQ:
            # ASSUMPTION: 같은 원고에 같은 화 번호. 명세에 전용 코드가 없어 공통 코드로 답한다.
            raise errors.InvalidInput("이미 있는 챕터 번호입니다", field="chapter_no") from exc
        raise
    return _chapter(row)


async def list_chapters(
    session: AsyncSession,
    project_id: UUID,
    limit: int,
    cursor: tuple[int, UUID] | None,
    manuscript_id: UUID | None = None,
) -> list[Chapter]:
    return await repo.list_chapters(session, project_id, limit, cursor, manuscript_id)


def to_chapter_responses(rows: Sequence[Chapter]) -> list[ChapterResponse]:
    return [_chapter(c) for c in rows]


async def _get_chapter(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> Chapter:
    chapter = await repo.get_chapter(session, project_id, chapter_id)
    if chapter is None:
        raise errors.ChapterNotFound()
    return chapter


async def get_chapter(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> ChapterResponse:
    return _chapter(await _get_chapter(session, project_id, chapter_id))


async def update_chapter(
    session: AsyncSession, project_id: UUID, chapter_id: UUID, body: ChapterUpdate
) -> ChapterResponse:
    chapter = await _get_chapter(session, project_id, chapter_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(chapter, field, value)
    try:
        await session.flush()
    except IntegrityError as exc:
        if errors.constraint_name(exc) == CHAPTER_NO_UQ:
            raise errors.InvalidInput("이미 있는 챕터 번호입니다", field="chapter_no") from exc
        raise
    return _chapter(chapter)


async def delete_chapter(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> None:
    chapter = await _get_chapter(session, project_id, chapter_id)
    _chapter_deleted(session, chapter)
    await session.delete(chapter)
    await session.flush()
