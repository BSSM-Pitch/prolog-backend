"""`passage_index` 잡 — 챕터 하나의 조각을 다시 만든다 (`app.ai.jobs.run` 위에).

prepare: 챕터 본문과 기존 조각의 (text_hash → 임베딩)을 읽는다.
call(스레드): 패키지로 조각을 나누고, text_hash 가 새로운 조각만 임베딩한다(배치 1회).
on_success: 그 챕터의 조각을 지우고 새로 넣는다. 잡 result 에는 개수만 남긴다 — 임베딩
(조각당 1536개 실수)을 jsonb 에 남기면 잡 테이블이 불어난다.

ponytail: 앞 챕터를 고쳐도 뒤 챕터는 다시 색인하지 않는다 — 조각은 챕터 안에서만 의미가 있어
1단계에는 문제가 없다. 2단계 digest 의 "앞 이야기" 입력이 생기면 스펙 §4.4 한계를 다시 본다.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import jobs as ai_jobs
from app.ai.client import AIClient, Result
from app.content.manuscripts.models import Chapter
from app.content.memory.models import Passage
from app.content.memory.service import JOB_TYPE, md5
from app.core import errors
from app.jobs.models import Job


async def _prepare(session: AsyncSession, job: Job) -> dict[str, Any]:
    chapter = await session.get(Chapter, cast(UUID, job.target_id))
    if chapter is None:
        raise errors.ChapterNotFound()
    # pgvector 는 설치된 패키지에 따라 numpy 배열이나 list 를 준다.
    # jsonb 로 흘러가므로 파이썬 float 로 통일한다.
    known = {
        text_hash: [float(x) for x in vector]
        for text_hash, vector in (
            await session.execute(
                select(Passage.text_hash, Passage.embedding).where(Passage.chapter_id == chapter.id)
            )
        ).all()
    }
    return {"text": chapter.content, "known": known}


def _call(ai: AIClient, args: Any) -> Result:
    split = ai.split_passages(args["text"])
    if "error" in split:
        return split
    pieces = split["data"]["passages"]
    for piece in pieces:
        piece["text_hash"] = md5(piece["text"])
    new_texts = list(
        dict.fromkeys(p["text"] for p in pieces if p["text_hash"] not in args["known"])
    )
    vectors: dict[str, list[float]] = dict(args["known"])
    if new_texts:
        embedded = ai.embed(new_texts)
        if "error" in embedded:
            return embedded
        vectors.update(zip((md5(t) for t in new_texts), embedded["data"]["vectors"], strict=True))
    for piece in pieces:
        piece["embedding"] = vectors[piece["text_hash"]]
    return {
        "data": {"passages": pieces, "source_hash": md5(args["text"])},
        "meta": {"embedded": len(new_texts)},
    }


async def _store(session: AsyncSession, job: Job, data: dict[str, Any]) -> None:
    chapter = await session.get(Chapter, cast(UUID, job.target_id))
    if chapter is None:
        return
    # 낡은 잡이 더 새 본문으로 만든 조각을 덮으면 안 된다. 현재 본문은 스위퍼가 다시 색인한다.
    if data["source_hash"] != md5(chapter.content):
        return
    await session.execute(delete(Passage).where(Passage.chapter_id == chapter.id))
    for piece in data["passages"]:
        session.add(
            Passage(
                project_id=chapter.project_id,
                manuscript_id=chapter.manuscript_id,
                chapter_id=chapter.id,
                seq=piece["seq"],
                start_offset=piece["start"],
                end_offset=piece["end"],
                text=piece["text"],
                text_hash=piece["text_hash"],
                source_hash=data["source_hash"],
                embedding=piece["embedding"],
            )
        )
    # 잡 완료 전이(조건부 UPDATE)는 이미 이겼다. 같은 트랜잭션에서 result 를 개수로 줄인다.
    job.result = {"passage_count": len(data["passages"]), "meta": data.get("meta") or {}}


async def handle(sessions: Any, job_id: UUID, ai: AIClient) -> ai_jobs.Outcome:
    return await ai_jobs.run(
        sessions, job_id, JOB_TYPE, ai, prepare=_prepare, call=_call, on_success=_store
    )
