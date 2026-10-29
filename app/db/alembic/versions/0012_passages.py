"""원고 기억 1단계 — 원문 조각(passages) · 색인 잡 · AIQ 인용.

스펙: prolog-ai/docs/superpowers/specs/2026-10-19-story-memory-design.md §3.1.
스펙은 `memory` 스키마를 제안했지만 1단계는 테이블 하나라 `content` 에 둔다(테스트의 스키마 목록 ·
TRUNCATE 대상이 그대로다). 2단계에서 digest 테이블이 붙을 때 옮길지 다시 본다.

- `chapter_id` 는 단일 FK CASCADE — 챕터가 지워지면 조각도 사라진다. 원고 FK 는 다른 content
  테이블처럼 (id, project_id) 복합이다.
- `source_hash` = md5(챕터 본문). 현재 본문의 md5 와 다르면 그 조각은 낡았다.
- `text_hash` = md5(조각 본문). 다시 색인할 때 같은 조각의 임베딩을 재사용한다.
- 벡터 인덱스는 없다. 검색은 manuscript_id 로 거른 뒤 코사인 거리로 정렬한다(정확 검색).
- ponytail: exact per-manuscript scan (filter by manuscript_id via btree, then sort by cosine
  distance) — pgvector's HNSW applies the manuscript filter after the approximate scan and can
  return fewer than k hits; add HNSW with `SET LOCAL hnsw.iterative_scan` when cross-manuscript
  search or very large manuscripts arrive.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPES = (
    "'nl_extraction','rule_extraction','structure_analysis',"
    "'conflict_check','qa_answer','manuscript_extraction'"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
CREATE TABLE content.passages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    manuscript_id uuid NOT NULL,
    chapter_id uuid NOT NULL REFERENCES content.chapters(id) ON DELETE CASCADE,
    seq integer NOT NULL,
    start_offset integer NOT NULL,
    end_offset integer NOT NULL,
    text text NOT NULL,
    text_hash varchar(32) NOT NULL,
    source_hash varchar(32) NOT NULL,
    embedding vector(1536) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT passages_manuscript_fk FOREIGN KEY (manuscript_id, project_id)
        REFERENCES content.manuscripts(id, project_id) ON DELETE CASCADE,
    CONSTRAINT passages_chapter_seq_uq UNIQUE (chapter_id, seq),
    CONSTRAINT passages_offsets_chk CHECK (0 <= start_offset AND start_offset < end_offset)
)
"""
    )
    op.execute("CREATE INDEX passages_manuscript_idx ON content.passages (manuscript_id)")
    op.execute(
        "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON content.passages "
        "FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at()"
    )
    op.execute(
        "ALTER TABLE ops.jobs DROP CONSTRAINT jobs_job_type_chk, "
        f"ADD CONSTRAINT jobs_job_type_chk CHECK (job_type IN ({JOB_TYPES},'passage_index'))"
    )
    op.execute("ALTER TABLE insight.qa_messages ADD COLUMN citations jsonb NOT NULL DEFAULT '[]'")


def downgrade() -> None:
    op.execute("ALTER TABLE insight.qa_messages DROP COLUMN citations")
    op.execute("DELETE FROM ops.jobs WHERE job_type = 'passage_index'")
    op.execute(
        "ALTER TABLE ops.jobs DROP CONSTRAINT jobs_job_type_chk, "
        f"ADD CONSTRAINT jobs_job_type_chk CHECK (job_type IN ({JOB_TYPES}))"
    )
    op.execute("DROP TABLE content.passages")
