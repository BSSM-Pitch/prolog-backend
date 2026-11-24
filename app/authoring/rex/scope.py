"""Bound and snapshot only the manuscript text explicitly requested for REX."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts import service as manuscripts
from app.content.manuscripts.models import Chapter
from app.core import errors

MAX_EXTRACTION_CHARS = 30_000


def validate_text(text: str) -> None:
    if not text.strip():
        raise errors.InvalidInput("분석할 본문이 있는 장을 선택해 주세요", field="chapter_ids")
    if len(text) > MAX_EXTRACTION_CHARS:
        raise errors.InvalidInput(
            "한 번에 30,000자까지 분석할 수 있어요. 분석할 장을 줄여 주세요.",
            field="chapter_ids",
            char_count=len(text),
            max_chars=MAX_EXTRACTION_CHARS,
        )


async def snapshot(
    session: AsyncSession,
    project_id: UUID,
    manuscript_id: UUID,
    chapter_ids: list[UUID] | None,
) -> dict[str, Any]:
    manuscript = await manuscripts.get(session, project_id, manuscript_id)
    if manuscript.status != "ready":
        raise errors.InvalidInput(
            "본문이 준비된 원고만 규칙을 추출할 수 있습니다", field="manuscript_id"
        )
    stmt = select(Chapter).where(
        Chapter.project_id == project_id, Chapter.manuscript_id == manuscript_id
    )
    if chapter_ids is not None:
        if not chapter_ids or len(chapter_ids) != len(set(chapter_ids)):
            raise errors.InvalidInput("분석할 장을 중복 없이 선택해 주세요", field="chapter_ids")
        stmt = stmt.where(Chapter.id.in_(chapter_ids))
    chapters = list((await session.scalars(stmt.order_by(Chapter.chapter_no, Chapter.id))).all())
    if chapter_ids is not None and len(chapters) != len(chapter_ids):
        raise errors.InvalidInput(
            "선택한 장을 이 원고에서 찾을 수 없어요. 목록을 새로 확인해 주세요.",
            field="chapter_ids",
        )
    text = (
        "\n\n".join(c.content for c in chapters)
        if chapter_ids is not None
        else manuscript.content or ""
    )
    validate_text(text)
    return {
        "scope_version": 1,
        "text": text,
        "chapters": [
            {"chapter_id": str(c.id), "chapter_no": c.chapter_no, "content": c.content}
            for c in chapters
        ],
        "chapter_ids": [str(c.id) for c in chapters] if chapter_ids is not None else None,
        "char_count": len(text),
    }
