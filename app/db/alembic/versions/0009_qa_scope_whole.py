"""AIQ scope — 원고 전체를 `project` 가 아니라 `whole` 로 부른다(명세 §2.1 · 화면 02).

스레드는 원고에 딸린다(`qa_threads.manuscript_id`). 원고 전체에 대한 질문을 `project` 로 저장하면
이름이 거짓이다. 명세와 화면("전체 원고 / 문장 선택")의 어휘로 맞춘다. `chapter` 는 화면에 없어
API 에 내지 않지만 `chapter_id` 와 함께 남긴다. 패키지에는 `whole` 을 `project` 로 넘긴다
(prolog-ai README: project · chapter 는 넘긴 본문 전체를 본다).

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE insight.qa_threads DROP CONSTRAINT qa_threads_scope_chk;
UPDATE insight.qa_threads SET scope = 'whole' WHERE scope = 'project';
ALTER TABLE insight.qa_threads
    ADD CONSTRAINT qa_threads_scope_chk CHECK (scope IN ('whole','chapter','selection'));
"""

DOWNGRADE = """
ALTER TABLE insight.qa_threads DROP CONSTRAINT qa_threads_scope_chk;
UPDATE insight.qa_threads SET scope = 'project' WHERE scope = 'whole';
ALTER TABLE insight.qa_threads
    ADD CONSTRAINT qa_threads_scope_chk CHECK (scope IN ('project','chapter','selection'));
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
