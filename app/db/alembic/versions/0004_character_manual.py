"""캐릭터 수동 경로 — ERD 와 남은 차이를 메운다.

정본: Notion `03. authoring — 초안 · 캐릭터 · 세계관 규칙`.

- `character_drafts.job_id` → `source_job_id` (ERD 이름). 사용자가 빈 초안을 만들면 NULL.
- `character_drafts.source_text` NULL 허용. 빈 초안에는 자연어 원문이 없다.
- `characters.created_from_draft_id` · `confirmed_at` (ERD). 기존 행이 없으므로 NOT NULL 을
  바로 건다(DEFAULT now() 는 기존 행 채움용이 아니라 INSERT 편의다).
- `character_edit_histories.item_id` (ERD 에 없음). 화면의 "편집 이력 N건 펼치기" 는 **항목 하나**의
  이력이다 — 초안 id 만으로는 항목별로 가를 수 없다. FK 를 걸지 않는다: 항목을 지워도 이력은
  남아야 한다(append-only). 확정 후 이력에서는 character_attributes.id 를 가리킨다.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE authoring.character_drafts RENAME COLUMN job_id TO source_job_id;
ALTER TABLE authoring.character_drafts ALTER COLUMN source_text DROP NOT NULL;

ALTER TABLE authoring.characters
    ADD COLUMN created_from_draft_id uuid
        REFERENCES authoring.character_drafts(id) ON DELETE SET NULL,
    ADD COLUMN confirmed_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE authoring.character_edit_histories ADD COLUMN item_id uuid;
CREATE INDEX character_edit_histories_draft_item_idx
    ON authoring.character_edit_histories (draft_id, item_id, created_at);
"""

DOWNGRADE = """
DROP INDEX authoring.character_edit_histories_draft_item_idx;
ALTER TABLE authoring.character_edit_histories DROP COLUMN item_id;
ALTER TABLE authoring.characters
    DROP COLUMN confirmed_at,
    DROP COLUMN created_from_draft_id;
UPDATE authoring.character_drafts SET source_text = '' WHERE source_text IS NULL;
ALTER TABLE authoring.character_drafts ALTER COLUMN source_text SET NOT NULL;
ALTER TABLE authoring.character_drafts RENAME COLUMN source_job_id TO job_id;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
