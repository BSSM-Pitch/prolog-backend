"""Allow resumable character-memory jobs. Memory and checkpoints live in ops.jobs.result."""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

OLD_TYPES = (
    "'nl_extraction','rule_extraction','structure_analysis',"
    "'conflict_check','qa_answer','manuscript_extraction'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE ops.jobs DROP CONSTRAINT jobs_job_type_chk")
    op.execute(
        "ALTER TABLE ops.jobs ADD CONSTRAINT jobs_job_type_chk "
        f"CHECK (job_type IN ({OLD_TYPES},'character_memory'))"
    )


def downgrade() -> None:
    # Refuse to silently delete memories. An operator must export/remove these jobs first.
    op.execute("ALTER TABLE ops.jobs DROP CONSTRAINT jobs_job_type_chk")
    op.execute(
        f"ALTER TABLE ops.jobs ADD CONSTRAINT jobs_job_type_chk CHECK (job_type IN ({OLD_TYPES}))"
    )
