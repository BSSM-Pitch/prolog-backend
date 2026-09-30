"""원고 편집 이력 — 스냅샷을 언제 뜨는가 (ROADMAP §12-3, 화면 32).

**정책.** ERD 의 기준("마지막 스냅샷으로부터 5분 경과 또는 변경량 1,000자 이상일 때만
기록")을 따르되, 기준에 못 미치는 저장은 버리지 않고 **마지막 편집기 스냅샷을 덮어쓴다.**
버리면 창의 마지막 편집이 다음 편집이 올 때까지 이력에 없다 — 덮어쓰면 창마다 마지막 상태가
남고 행 수는 똑같이 묶인다.

- 업로드 추출이 끝나면 **항상** 새 스냅샷(`upload`). 화면의 "N차 원고 불러옴" 이다.
- 편집기 저장(`PATCH content`):
  - 내용이 마지막 스냅샷과 같으면 아무것도 하지 않는다.
  - 마지막이 편집기 스냅샷이고, 만든 지 `WINDOW` 미만이고, 길이 변화가 `CHARS` 미만이면 덮어쓴다.
  - 아니면 새 스냅샷(`editor`).
- 제목만 바꾸면 스냅샷이 없다. 업로드 스냅샷은 덮어쓰지 않는다.

변경량은 **길이 차이**로 잰다. 편집 거리는 장편에서 저장마다 O(n·m) 이라 쓰지 않는다 — 같은 길이로
바꿔 쓴 대량 수정은 5분 창에서 한 번으로 묶인다.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts.models import Manuscript, ManuscriptVersion

WINDOW = timedelta(minutes=5)
CHARS = 1000


async def _latest(session: AsyncSession, manuscript_id: UUID) -> ManuscriptVersion | None:
    stmt = (
        select(ManuscriptVersion)
        .where(ManuscriptVersion.manuscript_id == manuscript_id)
        .order_by(ManuscriptVersion.version_no.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def snapshot(
    session: AsyncSession,
    manuscript: Manuscript,
    source: str,
    user_id: UUID | None,
    *,
    now: datetime | None = None,
) -> ManuscriptVersion | None:
    """정책대로 스냅샷을 만들거나 덮어쓴다. 부르는 쪽이 **원고 행을 잠근 상태**여야 한다.

    잠그지 않으면 동시 저장 둘이 같은 `version_no` 를 잡는다(UNIQUE 가 막지만 500 이 된다).
    """
    text = manuscript.content or ""
    last = await _latest(session, manuscript.id)
    if last is not None and last.content == text:
        return None
    now = now or datetime.now(UTC)
    if (
        source == "editor"
        and last is not None
        and last.source == "editor"
        and now - last.created_at < WINDOW
        and abs(len(text) - last.char_count) < CHARS
    ):
        last.content, last.char_count, last.created_by = text, len(text), user_id
        return last
    version = ManuscriptVersion(
        project_id=manuscript.project_id,
        manuscript_id=manuscript.id,
        version_no=1 if last is None else last.version_no + 1,
        content=text,
        char_count=len(text),
        created_by=user_id,
        source=source,
    )
    session.add(version)
    return version


async def list_versions(
    session: AsyncSession, manuscript_id: UUID, limit: int, cursor: tuple[int, UUID] | None
) -> list[ManuscriptVersion]:
    """최신부터. 커서는 (version_no, id) 다 — 덮어쓴 스냅샷도 자리를 지킨다."""
    stmt = (
        select(ManuscriptVersion)
        .where(ManuscriptVersion.manuscript_id == manuscript_id)
        .order_by(ManuscriptVersion.version_no.desc(), ManuscriptVersion.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(
            tuple_(ManuscriptVersion.version_no, ManuscriptVersion.id)
            < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())
