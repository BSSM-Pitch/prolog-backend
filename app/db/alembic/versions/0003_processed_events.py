"""이벤트 소비 멱등 — ops.processed_events.

릴레이는 at-least-once 다. 같은 outbox 이벤트가 두 번 오면 notifier 가 알림을 두 번 만들었다
(`scripts/verify/durability.py dup` 으로 재현). 소비자는 부작용과 **같은 트랜잭션**에서
(consumer, event_id) 를 INSERT 하고, 이미 있으면 건너뛴다.

`jobs.idempotency_key` 와는 다른 것이다 — 그쪽은 잡 **생성** 중복(클라이언트 이중 요청)을 막는다.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TABLE ops.processed_events (
    consumer varchar(50) NOT NULL,
    event_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, event_id)
);
-- 0001 의 루프는 그 시점의 테이블에만 트리거를 달았다. 새 테이블은 직접 단다.
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON ops.processed_events
    FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
"""

DOWNGRADE = """
DROP TABLE ops.processed_events;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
