"""복선(FTS) — ERD 어휘로 맞추고, 챕터 삭제를 이벤트로 받게 한다.

정본: Notion `04. insight` §4.3 · FTS 명세 12항.

- `foreshadowings.status`: `planted|resolved|abandoned` → `unresolved|resolved|orphaned` (ERD).
- `foreshadowing_chapters.role`: `setup|payoff|hint` → `setup|payoff|linked`
  (ERD · 명세 §2.4).
- **`foreshadowing_chapters.chapter_id` 의 FK 를 없앤다.** ERD 는 `ON DELETE RESTRICT`
  인데, 그러면 설치 챕터가 지워질 수 없어 `orphaned` 가 생기지 않는다. RESTRICT 를 지키려면
  MSU(Ring 2)가 삭제 전에 FTS(Ring 4)를 확인해야 한다 — 역방향 동기 호출이다. CASCADE 면
  이벤트가 닿기 전에 "어느 복선이 이 챕터를 가리켰는가" 가 사라진다. 그래서 FK 없이 두고,
  MSU 가 남긴 `chapter.deleted` 를 FTS 가 받아 정리한다(`app.insight.fts.events`).
  존재·테넌시 검사는 쓰기 시점에 앱이 한다.
- **`setup_chapter_no`·`payoff_chapter_no` 캐시를 없앤다.** 챕터 번호는 MSU 에서 바뀐다
  (PATCH chapter_no). 캐시를 갱신하려면 또 역방향 호출이 필요하다. FTS 는 Ring 4 라
  `content.chapters` 를 조인할 수 있다. 순서 CHECK(`payoff >= setup`)는 앱의
  `INVALID_PAYOFF_CHAPTER` 가 맡는다.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE insight.foreshadowings
    DROP CONSTRAINT foreshadowings_status_chk,
    DROP CONSTRAINT foreshadowings_payoff_order_chk;
UPDATE insight.foreshadowings SET status = CASE status
    WHEN 'planted' THEN 'unresolved' WHEN 'abandoned' THEN 'orphaned' ELSE status END;
ALTER TABLE insight.foreshadowings
    ALTER COLUMN status SET DEFAULT 'unresolved',
    ADD CONSTRAINT foreshadowings_status_chk
        CHECK (status IN ('unresolved','resolved','orphaned')),
    DROP COLUMN setup_chapter_no,
    DROP COLUMN payoff_chapter_no;
CREATE INDEX foreshadowings_project_status_idx ON insight.foreshadowings (project_id, status);

ALTER TABLE insight.foreshadowing_chapters
    DROP CONSTRAINT foreshadowing_chapters_role_chk,
    DROP CONSTRAINT foreshadowing_chapters_chapter_id_fkey;
UPDATE insight.foreshadowing_chapters SET role = 'linked' WHERE role = 'hint';
ALTER TABLE insight.foreshadowing_chapters
    ADD CONSTRAINT foreshadowing_chapters_role_chk CHECK (role IN ('setup','payoff','linked'));
"""

# 되돌리면 FK 가 돌아온다 — 이미 지워진 챕터를 가리키는 행(이벤트 처리 전)은 먼저 지운다.
# 캐시 컬럼은 비어 있는 채로 돌아온다.
DOWNGRADE = """
DELETE FROM insight.foreshadowing_chapters fc
WHERE NOT EXISTS (SELECT 1 FROM content.chapters c WHERE c.id = fc.chapter_id);
ALTER TABLE insight.foreshadowing_chapters DROP CONSTRAINT foreshadowing_chapters_role_chk;
UPDATE insight.foreshadowing_chapters SET role = 'hint' WHERE role = 'linked';
ALTER TABLE insight.foreshadowing_chapters
    ADD CONSTRAINT foreshadowing_chapters_role_chk CHECK (role IN ('setup','payoff','hint')),
    ADD CONSTRAINT foreshadowing_chapters_chapter_id_fkey
        FOREIGN KEY (chapter_id) REFERENCES content.chapters(id) ON DELETE RESTRICT;

DROP INDEX insight.foreshadowings_project_status_idx;
ALTER TABLE insight.foreshadowings DROP CONSTRAINT foreshadowings_status_chk;
UPDATE insight.foreshadowings SET status = CASE status
    WHEN 'unresolved' THEN 'planted' WHEN 'orphaned' THEN 'abandoned' ELSE status END;
ALTER TABLE insight.foreshadowings
    ALTER COLUMN status SET DEFAULT 'planted',
    ADD CONSTRAINT foreshadowings_status_chk
        CHECK (status IN ('planted','resolved','abandoned')),
    ADD COLUMN setup_chapter_no integer,
    ADD COLUMN payoff_chapter_no integer,
    ADD CONSTRAINT foreshadowings_payoff_order_chk
        CHECK (payoff_chapter_no IS NULL OR setup_chapter_no IS NULL
               OR payoff_chapter_no >= setup_chapter_no);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
