"""Preserve accounts and their content while denying authentication."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("banned_at", sa.DateTime(timezone=True)), schema="platform")
    op.add_column("users", sa.Column("ban_reason", sa.String(500)), schema="platform")


def downgrade() -> None:
    op.drop_column("users", "ban_reason", schema="platform")
    op.drop_column("users", "banned_at", schema="platform")
