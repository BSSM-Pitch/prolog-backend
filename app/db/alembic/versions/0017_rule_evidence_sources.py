"""Keep all source references when rule candidates merge into confirmed rules."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "world_rules",
        sa.Column("evidence_sources", JSONB, nullable=False, server_default="[]"),
        schema="authoring",
    )


def downgrade() -> None:
    op.drop_column("world_rules", "evidence_sources", schema="authoring")
