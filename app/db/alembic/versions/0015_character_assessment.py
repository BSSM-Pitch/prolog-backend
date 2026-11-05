"""Allow evidence-anchored character assessments."""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

OLD_TYPES = (
    "'nl_extraction','rule_extraction','structure_analysis','conflict_check',"
    "'qa_answer','manuscript_extraction','character_memory'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE ops.jobs DROP CONSTRAINT jobs_job_type_chk")
    op.execute(
        "ALTER TABLE ops.jobs ADD CONSTRAINT jobs_job_type_chk "
        f"CHECK (job_type IN ({OLD_TYPES},'character_assessment'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ops.jobs DROP CONSTRAINT jobs_job_type_chk")
    op.execute(
        f"ALTER TABLE ops.jobs ADD CONSTRAINT jobs_job_type_chk CHECK (job_type IN ({OLD_TYPES}))"
    )
