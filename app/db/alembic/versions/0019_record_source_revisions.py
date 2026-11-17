"""Source baselines for manual and AI-adopted relationship/foreshadowing records."""

import hashlib
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("foreshadowings", "relationship_histories"):
        op.add_column(table, sa.Column("source_snapshot", JSONB, nullable=True), schema="insight")
    # Reconstruct known historical baselines, never assume today's source was verified.
    bind = op.get_bind()
    jobs = (
        bind.execute(
            sa.text("""
        SELECT project_id, input, result->'writer_adoptions' AS adopted, created_at
        FROM ops.jobs WHERE input->>'review_mode' = 'narrative-v1'
        AND result ? 'writer_adoptions' ORDER BY updated_at DESC
    """)
        )
        .mappings()
        .all()
    )
    for job in jobs:
        source = job["input"]
        baseline = json.dumps(
            {
                "manuscript_id": source["manuscript_id"],
                "chapters": {
                    c["chapter_id"]: {
                        "chapter_no": c["chapter_no"],
                        "hash": hashlib.sha256(
                            f"{c['chapter_no']}\n{c.get('title') or ''}\n{c['content']}".encode()
                        ).hexdigest(),
                    }
                    for c in source["chapters"]
                },
            }
        )
        for entry in job["adopted"].values():
            params = {
                "source": baseline,
                "id": entry["target_id"],
                "pid": job["project_id"],
                "created": job["created_at"],
            }
            if entry["kind"] == "foreshadowing":
                bind.execute(
                    sa.text("""
                    UPDATE insight.foreshadowings SET source_snapshot=CAST(:source AS jsonb)
                    WHERE id=CAST(:id AS uuid) AND project_id=:pid AND source_snapshot IS NULL
                """),
                    params,
                )
            elif entry["kind"] == "relationship":
                # Old adoption metadata has no chapter_id. Only a single, newly-created history
                # can be attributed unambiguously; other legacy records remain unverified.
                bind.execute(
                    sa.text("""
                    UPDATE insight.relationship_histories h
                    SET source_snapshot=CAST(:source AS jsonb)
                    WHERE h.relationship_id=CAST(:id AS uuid) AND h.project_id=:pid
                    AND h.source_snapshot IS NULL AND h.created_at >= :created
                    AND (SELECT count(*) FROM insight.relationship_histories x
                         WHERE x.relationship_id=h.relationship_id)=1
                    AND (CAST(:source AS jsonb)->'chapters') ? h.chapter_id::text
                """),
                    params,
                )


def downgrade() -> None:
    for table in ("foreshadowings", "relationship_histories"):
        op.drop_column(table, "source_snapshot", schema="insight")
