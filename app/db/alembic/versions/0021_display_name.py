"""Allow duplicate display names and identify local accounts by email.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 already enforces lower(email) and (auth_provider, provider_user_id) uniqueness.
    # Keep those indexes, including their names used to translate signup conflicts.
    op.execute("ALTER TABLE platform.users ALTER COLUMN provider_user_id DROP NOT NULL")
    op.execute("UPDATE platform.users SET provider_user_id = NULL WHERE auth_provider = 'local'")
    op.execute(
        "ALTER TABLE platform.users ADD CONSTRAINT users_provider_user_id_chk "
        "CHECK ((auth_provider = 'local') = (provider_user_id IS NULL))"
    )
    op.execute(
        "ALTER TABLE platform.users ADD CONSTRAINT users_local_email_chk "
        "CHECK (auth_provider <> 'local' OR (email IS NOT NULL AND btrim(email) <> ''))"
    )
    op.execute("ALTER TABLE platform.users DROP CONSTRAINT users_username_uq")


def downgrade() -> None:
    # Fail transactionally if duplicate nicknames now exist; never rename or delete accounts.
    op.execute("ALTER TABLE platform.users ADD CONSTRAINT users_username_uq UNIQUE (username)")
    op.execute("ALTER TABLE platform.users DROP CONSTRAINT users_local_email_chk")
    op.execute("ALTER TABLE platform.users DROP CONSTRAINT users_provider_user_id_chk")
    op.execute(
        "UPDATE platform.users SET provider_user_id = username WHERE auth_provider = 'local'"
    )
    op.execute("ALTER TABLE platform.users ALTER COLUMN provider_user_id SET NOT NULL")
