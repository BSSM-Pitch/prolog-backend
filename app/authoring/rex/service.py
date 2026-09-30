"""REX (Ring 3) — 세계관 규칙 CRUD. AI 추출(rule-extractions)은 2b 다.

규칙은 SCDS 가 읽는 공유 리소스다. 삭제는 물리 삭제이고, SCDS 룰 필터는 매번
`violation_keywords` GIN 인덱스로 읽으므로 지운 규칙은 즉시 판정에서 빠진다(명세 §4.8).
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.authoring.rex.models import WorldRule
from app.authoring.rex.schemas import WorldRuleCreate, WorldRuleResponse, WorldRuleUpdate
from app.core import errors


def to_responses(rows: Sequence[WorldRule]) -> list[WorldRuleResponse]:
    return [
        WorldRuleResponse(
            rule_id=r.id,
            project_id=r.project_id,
            title=r.title,
            description=r.description,
            violation_keywords=list(r.violation_keywords),
            origin=r.origin,  # type: ignore[arg-type]
            extraction_id=r.extraction_job_id,
            evidence=r.evidence,
            source_chapter_no=r.source_chapter_no,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


async def _get(session: AsyncSession, project_id: UUID, rule_id: UUID) -> WorldRule:
    stmt = select(WorldRule).where(WorldRule.id == rule_id, WorldRule.project_id == project_id)
    rule = (await session.execute(stmt)).scalar_one_or_none()
    if rule is None:
        raise errors.WorldRuleNotFound()
    return rule


async def _one(session: AsyncSession, rule: WorldRule) -> WorldRuleResponse:
    await session.flush()
    await session.refresh(rule)
    return to_responses([rule])[0]


async def list_rules(
    session: AsyncSession, project_id: UUID, limit: int, cursor: tuple[datetime, UUID] | None
) -> list[WorldRule]:
    """먼저 만든 규칙부터 — 화면의 R01, R02 … 순서다."""
    stmt = (
        select(WorldRule)
        .where(WorldRule.project_id == project_id)
        .order_by(WorldRule.created_at, WorldRule.id)
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(
            tuple_(WorldRule.created_at, WorldRule.id)
            > tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    return list((await session.execute(stmt)).scalars())


async def create_rule(
    session: AsyncSession, project_id: UUID, body: WorldRuleCreate
) -> WorldRuleResponse:
    rule = WorldRule(
        project_id=project_id,
        title=body.title,
        description=body.description,
        violation_keywords=body.violation_keywords,
        origin="user_added",
    )
    session.add(rule)
    return await _one(session, rule)


async def update_rule(
    session: AsyncSession, project_id: UUID, rule_id: UUID, body: WorldRuleUpdate
) -> WorldRuleResponse:
    """AI 추출본도 자유롭게 고친다(§4.7). `origin`·`evidence` 는 그대로 — 출처는 바뀌지 않는다."""
    rule = await _get(session, project_id, rule_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(rule, field, value)
    return await _one(session, rule)


async def delete_rule(session: AsyncSession, project_id: UUID, rule_id: UUID) -> None:
    await session.delete(await _get(session, project_id, rule_id))
    await session.flush()
