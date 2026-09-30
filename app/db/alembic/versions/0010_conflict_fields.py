"""SCDS 충돌 — 명세 §2.7 필드와 억제 범위를 맞춘다.

- `conflicts.status`: `open` → `pending`(명세 · ERD). 부분 인덱스도 같이.
- `conflicts` 에 명세 필드를 더한다: `chapter_id`(ERD · 필터), `conflict_target`(충돌한 설정 문구),
  `matched_keyword`(룰이 걸린 키워드), `modified_content`(`modified` 처리 내용), `resolved_at`.
  `chapter_id` 는 FK 를 걸지 않는다 — 사건이 챕터와 CASCADE 이고 충돌은 사건과 CASCADE 다.
- `conflict_suppressions.suppression_key` 의 UNIQUE 가 **전역**이었다. ERD 는
  `UNIQUE (project_id, suppression_key)` — 프로젝트별로 바꾼다.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
DROP INDEX insight.conflicts_project_open_idx;
ALTER TABLE insight.conflicts DROP CONSTRAINT conflicts_status_chk;
UPDATE insight.conflicts SET status = 'pending' WHERE status = 'open';
ALTER TABLE insight.conflicts
    ALTER COLUMN status SET DEFAULT 'pending',
    ADD CONSTRAINT conflicts_status_chk
        CHECK (status IN ('pending','accepted','ignored','modified')),
    ADD COLUMN chapter_id uuid,
    ADD COLUMN conflict_target text,
    ADD COLUMN matched_keyword text,
    ADD COLUMN modified_content text,
    ADD COLUMN resolved_at timestamptz,
    ADD CONSTRAINT conflicts_modified_chk
        CHECK (status <> 'modified' OR modified_content IS NOT NULL);
CREATE INDEX conflicts_project_status_idx
    ON insight.conflicts (project_id, status, created_at DESC);
CREATE INDEX conflicts_job_idx ON insight.conflicts (job_id);

ALTER TABLE insight.conflict_suppressions
    DROP CONSTRAINT conflict_suppressions_suppression_key_key,
    ADD CONSTRAINT conflict_suppressions_project_key_uq UNIQUE (project_id, suppression_key);
"""

DOWNGRADE = """
ALTER TABLE insight.conflict_suppressions
    DROP CONSTRAINT conflict_suppressions_project_key_uq;
DELETE FROM insight.conflict_suppressions a USING insight.conflict_suppressions b
    WHERE a.suppression_key = b.suppression_key AND a.id > b.id;
ALTER TABLE insight.conflict_suppressions
    ADD CONSTRAINT conflict_suppressions_suppression_key_key UNIQUE (suppression_key);

DROP INDEX insight.conflicts_job_idx;
DROP INDEX insight.conflicts_project_status_idx;
ALTER TABLE insight.conflicts
    DROP CONSTRAINT conflicts_modified_chk,
    DROP COLUMN resolved_at,
    DROP COLUMN modified_content,
    DROP COLUMN matched_keyword,
    DROP COLUMN conflict_target,
    DROP COLUMN chapter_id,
    DROP CONSTRAINT conflicts_status_chk;
UPDATE insight.conflicts SET status = 'open' WHERE status = 'pending';
ALTER TABLE insight.conflicts
    ALTER COLUMN status SET DEFAULT 'open',
    ADD CONSTRAINT conflicts_status_chk
        CHECK (status IN ('open','accepted','ignored','modified'));
CREATE INDEX conflicts_project_open_idx
    ON insight.conflicts (project_id, created_at DESC) WHERE status = 'open';
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
