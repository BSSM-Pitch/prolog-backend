"""Persistent project covers and character portraits."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE content.project_images (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
            target_id uuid NOT NULL,
            kind varchar(16) NOT NULL CHECK (kind IN ('cover', 'portrait')),
            storage_key varchar(255) NOT NULL,
            width integer NOT NULL CHECK (width > 0),
            height integer NOT NULL CHECK (height > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(project_id, kind, target_id)
        );
        CREATE TRIGGER touch_updated_at BEFORE UPDATE ON content.project_images
            FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
    """)


def downgrade() -> None:
    op.drop_table("project_images", schema="content")
