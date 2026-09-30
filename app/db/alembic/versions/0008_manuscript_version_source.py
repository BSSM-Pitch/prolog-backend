"""원고 스냅샷의 출처 — 화면 32 "편집 이력".

화면은 스냅샷마다 "파일 업로드(N차 원고 불러옴)" 와 "편집기에서 직접 수정 / 자동 저장" 을
구분한다. `source` 가 없으면 어느 쪽인지 알 수 없다. 디바운스도 출처를 본다 — 업로드 스냅샷은
덮어쓰지 않는다(`app.content.manuscripts.versions`).

`version_no` 는 원고 안에서 1 부터 오른다. 동시 저장이 같은 번호를 잡지 않게 UNIQUE 를 건다
(쓰는 쪽은 원고 행을 잠근다).

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE content.manuscript_versions
    ADD COLUMN source varchar(10) NOT NULL DEFAULT 'editor',
    ADD CONSTRAINT manuscript_versions_source_chk CHECK (source IN ('editor','upload')),
    ADD CONSTRAINT manuscript_versions_no_uq UNIQUE (manuscript_id, version_no);
ALTER TABLE content.manuscript_versions ALTER COLUMN source DROP DEFAULT;
"""

DOWNGRADE = """
ALTER TABLE content.manuscript_versions
    DROP CONSTRAINT manuscript_versions_no_uq,
    DROP CONSTRAINT manuscript_versions_source_chk,
    DROP COLUMN source;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
