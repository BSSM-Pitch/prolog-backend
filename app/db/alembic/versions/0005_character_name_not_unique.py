"""캐릭터 이름 중복을 허용한다 — 화면 37 "새 인물로 만들기".

화면이 "같은 이름의 인물이 하나 더 생겨요" 라고 알린 뒤 `create_new` 로 진행한다. 제약이 있으면
그 선택지가 409 가 되어 화면이 거짓말을 한다. ERD 의 두 선택지("이름 변경 강제 | 앱 레벨 경고로
완화") 중 후자다. 중복 판정은 앱이 한다(`ass.service._check_name`) — resolution 없이 확정하거나
이름을 바꿀 때만 409 다.

조회용으로 같은 식의 **일반** 부분 인덱스를 남긴다(중복 후보 검색).

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
DROP INDEX authoring.characters_project_name_active_uq;
CREATE INDEX characters_project_name_active_idx
    ON authoring.characters (project_id, lower(name)) WHERE status = 'active';
"""

# 되돌리면 UNIQUE 가 돌아온다. 이미 생긴 동명 캐릭터는 가장 먼저 만든 하나만 active 로 두고
# 나머지를 archived 로 내린다 — 지우지 않는다.
DOWNGRADE = """
UPDATE authoring.characters c SET status = 'archived'
WHERE c.status = 'active' AND EXISTS (
    SELECT 1 FROM authoring.characters o
    WHERE o.project_id = c.project_id AND lower(o.name) = lower(c.name) AND o.status = 'active'
      AND (o.created_at, o.id) < (c.created_at, c.id)
);
DROP INDEX authoring.characters_project_name_active_idx;
CREATE UNIQUE INDEX characters_project_name_active_uq
    ON authoring.characters (project_id, lower(name)) WHERE status = 'active';
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
