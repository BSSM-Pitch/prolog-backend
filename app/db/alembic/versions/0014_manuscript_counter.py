"""작품별 원고 생성 순번 — "N차 원고"의 N 이 지워도 줄지 않게.

예전에는 클라이언트가 `현재 원고 수 + 1` 로 제목을 붙여서, 원고를 지운 뒤 새로 만들면
이미 있는 번호와 겹치거나 실제로 만든 횟수와 어긋났다. 이제 서버가 작품마다 카운터를 두고
원고를 만들 때마다 1 씩 올린다. 원고가 삭제돼도 카운터는 그대로다.

기존 작품은 `원고 수` 와 제목에 이미 쓰인 가장 큰 "N차 원고" 중 큰 값으로 시작한다.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE content.manuscript_counters (
            project_id uuid PRIMARY KEY REFERENCES platform.projects(id) ON DELETE CASCADE,
            seq integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT manuscript_counters_seq_chk CHECK (seq >= 0)
        );
        INSERT INTO content.manuscript_counters (project_id, seq)
        SELECT project_id,
               GREATEST(
                   count(*),
                   COALESCE(max(substring(title from '^(\d{1,6})차 원고$')::integer), 0)
               )
        FROM content.manuscripts
        GROUP BY project_id;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE content.manuscript_counters")
