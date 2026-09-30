"""authoring 스키마를 ERD 에 맞춘다.

정본: Notion `03. authoring — 초안 · 캐릭터 · 세계관 규칙`.

**0001 직접 수정이 아니라 0002 인 이유**: dev DB 에 데이터가 있고 커밋이 쌓였다.
0001 을 고치면 이미 마이그레이션을 적용한 사람의 DB 와 파일이 조용히 어긋난다.

ERD 와 다르게 **그대로 둔 것**(현재 DDL 이 낫다고 판단, 명세 수정 제안으로 분류):
- `character_drafts.status` 의 `pending` (ERD 는 `editing`)
- `character_drafts.source_text` (ERD 에 없지만 화면의 자연어 원문을 담는다)
- `world_rules.title` · `category` (ERD 에 없지만 유용하다)

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES = "'personality_tag','core_value','influence_relation','emotion_keyword'"
ORIGINS = "'ai_extracted','user_added'"

UPGRADE = f"""
-- character_attributes: 근거·출처를 항목마다 갖는다. 카테고리는 NLCD 가 뽑는 4종.
ALTER TABLE authoring.character_attributes
    ADD COLUMN evidence text,
    ADD COLUMN origin varchar(20) NOT NULL,
    ALTER COLUMN value TYPE text,
    DROP CONSTRAINT character_attributes_category_chk,
    ADD CONSTRAINT character_attributes_category_chk CHECK (category IN ({CATEGORIES})),
    ADD CONSTRAINT character_attributes_origin_chk CHECK (origin IN ({ORIGINS}));

-- character_drafts: 확정 결과를 추적한다. merge 로 확정하면 기존 캐릭터 id 가 들어간다.
ALTER TABLE authoring.character_drafts
    ADD COLUMN name varchar(100),
    ADD COLUMN confirmed_character_id uuid
        REFERENCES authoring.characters(id) ON DELETE SET NULL,
    ADD COLUMN confirmed_at timestamptz;

-- character_draft_items: character_attributes 와 같은 모양이어야 확정 시 그대로 승계된다.
ALTER TABLE authoring.character_draft_items
    DROP COLUMN payload,
    DROP COLUMN selected,
    DROP COLUMN name,
    DROP COLUMN character_id,
    ADD COLUMN category varchar(30) NOT NULL,
    ADD COLUMN value text NOT NULL,
    ADD COLUMN evidence text,
    ADD COLUMN origin varchar(20) NOT NULL,
    ADD CONSTRAINT character_draft_items_category_chk CHECK (category IN ({CATEGORIES})),
    ADD CONSTRAINT character_draft_items_origin_chk CHECK (origin IN ({ORIGINS}));

-- character_edit_histories: 화면이 초안 단계에서도 항목별 이력을 보여준다.
-- before/after jsonb 로는 "무엇이 바뀌었는지" 를 질의할 수 없다.
ALTER TABLE authoring.character_edit_histories
    DROP COLUMN before,
    DROP COLUMN after,
    ADD COLUMN draft_id uuid REFERENCES authoring.character_drafts(id) ON DELETE CASCADE,
    ADD COLUMN phase varchar(10) NOT NULL,
    ADD COLUMN action varchar(10) NOT NULL,
    ADD COLUMN field varchar(50) NOT NULL,
    ADD COLUMN before_value text,
    ADD COLUMN after_value text,
    ALTER COLUMN character_id DROP NOT NULL,
    ADD CONSTRAINT character_edit_histories_phase_chk CHECK (phase IN ('draft','confirmed')),
    ADD CONSTRAINT character_edit_histories_action_chk
        CHECK (action IN ('create','update','delete')),
    ADD CONSTRAINT character_edit_histories_target_chk
        CHECK (character_id IS NOT NULL OR draft_id IS NOT NULL);
CREATE INDEX character_edit_histories_draft_idx
    ON authoring.character_edit_histories (draft_id, created_at DESC);

-- world_rules: origin 어휘를 ERD 에 맞추고(manual→user_added, ai→ai_extracted),
-- 규칙에 설명이 없을 수 없으므로 NOT NULL. 키워드 역색인은 SCDS 검출 핫패스다.
ALTER TABLE authoring.world_rules
    ALTER COLUMN description SET NOT NULL,
    ALTER COLUMN origin SET DEFAULT 'user_added',
    DROP CONSTRAINT world_rules_origin_chk,
    ADD CONSTRAINT world_rules_origin_chk CHECK (origin IN ({ORIGINS}));
CREATE INDEX world_rules_keywords_gin
    ON authoring.world_rules USING GIN (violation_keywords);
"""

# 되돌리면 재설계된 컬럼의 **데이터는 사라진다**(jsonb ↔ 개별 컬럼은 무손실 변환이 아니다).
# 구조만 0001 모양으로 복원한다.
DOWNGRADE = """
-- 0001 모양으로 옮길 수 없는 행을 먼저 치운다. 없으면 NOT NULL·CHECK 복원에서 멈춘다.
-- 재설계된 세 테이블은 버린다(위 주석대로 무손실 변환이 아니다). world_rules.origin 은 되돌린다.
DELETE FROM authoring.character_edit_histories;
DELETE FROM authoring.character_draft_items;
DELETE FROM authoring.character_attributes;
-- 값을 옛 어휘로 바꾸기 전에 새 어휘의 CHECK 를 먼저 뗀다(아래에서 옛 CHECK 를 단다).
ALTER TABLE authoring.world_rules DROP CONSTRAINT world_rules_origin_chk;
UPDATE authoring.world_rules
    SET origin = CASE origin WHEN 'ai_extracted' THEN 'ai' ELSE 'manual' END;

DROP INDEX authoring.world_rules_keywords_gin;
ALTER TABLE authoring.world_rules
    ALTER COLUMN description DROP NOT NULL,
    ALTER COLUMN origin SET DEFAULT 'manual',
    ADD CONSTRAINT world_rules_origin_chk CHECK (origin IN ('manual','ai'));

DROP INDEX authoring.character_edit_histories_draft_idx;
ALTER TABLE authoring.character_edit_histories
    DROP CONSTRAINT character_edit_histories_target_chk,
    DROP CONSTRAINT character_edit_histories_action_chk,
    DROP CONSTRAINT character_edit_histories_phase_chk,
    DROP COLUMN after_value,
    DROP COLUMN before_value,
    DROP COLUMN field,
    DROP COLUMN action,
    DROP COLUMN phase,
    DROP COLUMN draft_id,
    ALTER COLUMN character_id SET NOT NULL,
    ADD COLUMN before jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN after jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE authoring.character_draft_items
    DROP CONSTRAINT character_draft_items_origin_chk,
    DROP CONSTRAINT character_draft_items_category_chk,
    DROP COLUMN origin,
    DROP COLUMN evidence,
    DROP COLUMN value,
    DROP COLUMN category,
    ADD COLUMN name varchar(100) NOT NULL,
    ADD COLUMN payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN selected boolean NOT NULL DEFAULT false,
    ADD COLUMN character_id uuid REFERENCES authoring.characters(id) ON DELETE SET NULL;

ALTER TABLE authoring.character_drafts
    DROP COLUMN confirmed_at,
    DROP COLUMN confirmed_character_id,
    DROP COLUMN name;

ALTER TABLE authoring.character_attributes
    DROP CONSTRAINT character_attributes_origin_chk,
    DROP CONSTRAINT character_attributes_category_chk,
    ADD CONSTRAINT character_attributes_category_chk
        CHECK (category IN ('personality_tag','core_value')),
    ALTER COLUMN value TYPE varchar(100),
    DROP COLUMN origin,
    DROP COLUMN evidence;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
