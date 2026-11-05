"""Persist author-confirmed character aliases."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("aliases", JSONB, nullable=False, server_default="[]"),
        schema="authoring",
    )


def downgrade() -> None:
    op.drop_column("characters", "aliases", schema="authoring")
