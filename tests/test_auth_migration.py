"""Exercise the nickname migration with legacy rows inside a rolled-back transaction."""

from importlib import import_module

import psycopg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from tests.conftest import SYNC_URL


def test_migration_preserves_accounts_and_refuses_lossy_downgrade() -> None:
    migration = import_module("app.db.alembic.versions.0021_display_name")
    db = create_engine(SYNC_URL.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with db.connect() as conn, conn.begin() as transaction:
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                conn.exec_driver_sql(
                    "INSERT INTO platform.users "
                    "(email,auth_provider,provider_user_id,username,password_hash,role) VALUES "
                    "('legacy@ex.com','local','legacy','legacy','existing-hash','writer'),"
                    "(NULL,'google','stable-subject','social',NULL,'reader')"
                )
                before = conn.exec_driver_sql(
                    "SELECT id,email,username,password_hash FROM platform.users ORDER BY username"
                ).all()
                migration.upgrade()
                assert (
                    conn.exec_driver_sql(
                        "SELECT id,email,username,password_hash "
                        "FROM platform.users ORDER BY username"
                    ).all()
                    == before
                )
                assert (
                    conn.exec_driver_sql(
                        "SELECT provider_user_id FROM platform.users WHERE auth_provider='local'"
                    ).scalar()
                    is None
                )
                assert (
                    conn.exec_driver_sql(
                        "SELECT provider_user_id FROM platform.users WHERE auth_provider='google'"
                    ).scalar()
                    == "stable-subject"
                )
                conn.exec_driver_sql(
                    "INSERT INTO platform.users "
                    "(email,auth_provider,username,password_hash,role) VALUES "
                    "('second@ex.com','local','legacy','another-hash','writer')"
                )
                from sqlalchemy.exc import IntegrityError

                with pytest.raises(IntegrityError) as exc, conn.begin_nested():
                    migration.downgrade()
                assert isinstance(exc.value.orig, psycopg.errors.UniqueViolation)
                assert conn.exec_driver_sql("SELECT count(*) FROM platform.users").scalar() == 3
            transaction.rollback()
    finally:
        db.dispose()
