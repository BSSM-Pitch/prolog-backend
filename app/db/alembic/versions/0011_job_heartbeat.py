"""AI 잡 하트비트 — 오래 도는 잡을 좀비로 오판하지 않는다.

SSM 은 챕터마다 AI 를 부른다(호출당 최대 90초 × 3회). 장편이면 한 잡이 15분을 쉽게 넘는다.
좀비 판정을 `started_at` 으로 하면 살아서 도는 잡을 회수해 다시 돌린다(LLM 비용 두 배).

잡을 도는 워커가 `heartbeat_seconds` 마다 `heartbeat_at` 을 찍고, 스위퍼는 마지막 하트비트(없으면
`started_at`) 기준으로 판정한다. 워커가 죽으면 하트비트가 끊겨 곧 회수된다.

SQS visibility 를 연장하지 않는 이유: 정확성은 선점(조건부 UPDATE)이 지킨다 — visibility 가
지나 다른 워커가 같은 메시지를 받아도 선점에서 지고 지울 뿐이다.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE ops.jobs ADD COLUMN heartbeat_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE ops.jobs DROP COLUMN heartbeat_at")
