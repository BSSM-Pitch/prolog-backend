"""MSU 모듈 (Ring 2).

지금은 조합 레이어가 프로젝트 응답에 원고 수를 채울 때 쓰는 집계만 있다.
원고 CRUD 는 명세 충돌 확인 후에 붙인다 (보고 참조).
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts import repository as repo


async def manuscript_counts(session: AsyncSession, project_ids: Sequence[UUID]) -> dict[UUID, int]:
    """PRJ 명세 §2.1 의 `manuscript_count`.

    `content` 는 Ring 2 라서 `platform_.projects` 가 직접 셀 수 없다(규칙 1).
    조합 레이어가 이 함수를 호출해 프로젝트 응답에 합친다.
    """
    return await repo.count_by_projects(session, project_ids)
