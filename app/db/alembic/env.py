"""Alembic 은 동기 드라이버(psycopg)를 쓴다.

asyncpg 는 한 번의 execute 에 여러 문장을 담을 수 없어서 raw DDL 마이그레이션이 깨진다.
런타임(app)은 그대로 asyncpg 다.
"""

from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.all_models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("+asyncpg", "+psycopg"))
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
SCHEMAS = {"platform", "content", "authoring", "insight", "ops"}


def include_object(obj: Any, name: str, type_: str, reflected: bool, compare_to: Any) -> bool:
    if type_ == "table":
        return obj.schema in SCHEMAS
    return True


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,  # type: ignore[arg-type]
        **kwargs,
    )


if context.is_offline_mode():
    _configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
