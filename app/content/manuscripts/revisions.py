"""Cheap, deterministic source revisions shared by author-maintained records."""

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts.models import Chapter
from app.core import errors


def snapshot(manuscript_id: UUID, chapters: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "manuscript_id": str(manuscript_id),
        "chapters": {
            str(c["chapter_id"]): {
                "chapter_no": c["chapter_no"],
                "hash": hashlib.sha256(
                    f"{c['chapter_no']}\n{c.get('title') or ''}\n{c['content']}".encode()
                ).hexdigest(),
            }
            for c in chapters
        },
    }


class SourceReview(BaseModel):
    status: Literal["current", "changed", "unverified"]
    manuscript_id: UUID | None = None
    changed_chapters: list[int] = Field(default_factory=list)
    revision_token: str | None = None


class ConfirmSource(BaseModel):
    revision_token: str = Field(min_length=64, max_length=64)


class RevisionReader:
    """One instance per response: read/hash each manuscript once, not once per record."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.cache: dict[tuple[UUID, UUID], dict[str, Any]] = {}

    async def capture(self, project_id: UUID, manuscript_id: UUID) -> dict[str, Any]:
        key = (project_id, manuscript_id)
        if key not in self.cache:
            rows = (
                await self.session.scalars(
                    select(Chapter).where(
                        Chapter.project_id == project_id,
                        Chapter.manuscript_id == manuscript_id,
                    )
                )
            ).all()
            self.cache[key] = snapshot(
                manuscript_id,
                [
                    {
                        "chapter_id": c.id,
                        "chapter_no": c.chapter_no,
                        "title": c.title,
                        "content": c.content,
                    }
                    for c in rows
                ],
            )
        return self.cache[key]

    async def compare(
        self,
        project_id: UUID,
        baseline: dict[str, Any] | None,
        manuscript_id: UUID | None,
        signature: Any,
    ) -> SourceReview:
        if baseline:
            manuscript_id = UUID(baseline["manuscript_id"])
        if manuscript_id is None:
            return SourceReview(status="unverified")
        current = await self.capture(project_id, manuscript_id)
        token = hashlib.sha256(
            json.dumps(
                [current, signature],
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode()
        ).hexdigest()
        if not baseline:
            return SourceReview(
                status="unverified", manuscript_id=manuscript_id, revision_token=token
            )
        before, after = baseline["chapters"], current["chapters"]
        changed = sorted(
            {
                (after.get(key) or before[key])["chapter_no"]
                for key in before.keys() | after.keys()
                if before.get(key) != after.get(key)
            }
        )
        return SourceReview(
            status="changed" if changed else "current",
            manuscript_id=manuscript_id,
            changed_chapters=changed,
            revision_token=token,
        )


def verify_token(status: SourceReview, token: str) -> UUID:
    if status.manuscript_id is None or status.revision_token != token:
        raise errors.InvalidInput("원고나 기록이 다시 변경됐어요. 새로 불러온 뒤 확인해 주세요.")
    return status.manuscript_id
