"""마이그레이션(정본 DDL)과 SQLAlchemy 모델의 정합성 검사.

DDL 을 raw SQL 로 쓰는 대가로, 모델과 DB 가 어긋나는 순간을 여기서 잡는다.
"""

from sqlalchemy import text

from app.db.all_models import Base
from app.db.session import engine

EXPECTED_TABLE_COUNT = 37
SCHEMAS = ("platform", "content", "authoring", "insight", "ops")


async def test_all_38_tables_exist() -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema = ANY(:schemas) AND table_type = 'BASE TABLE'"
            ),
            {"schemas": list(SCHEMAS)},
        )
        actual = {r[0] for r in rows}
    expected = {f"{t.schema}.{t.name}" for t in Base.metadata.tables.values()}
    assert len(expected) == EXPECTED_TABLE_COUNT, sorted(expected)
    assert expected == actual


async def test_model_columns_match_database() -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_schema, table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = ANY(:schemas)"
            ),
            {"schemas": list(SCHEMAS)},
        )
        actual: dict[str, set[str]] = {}
        for schema, table, column in rows:
            actual.setdefault(f"{schema}.{table}", set()).add(column)

    for table in Base.metadata.tables.values():
        key = f"{table.schema}.{table.name}"
        model_columns = {c.name for c in table.columns}
        assert model_columns <= actual[key], (
            f"{key}: 모델에만 있는 컬럼 {model_columns - actual[key]}"
        )
        assert actual[key] <= model_columns, (
            f"{key}: DB 에만 있는 컬럼 {actual[key] - model_columns}"
        )


async def test_updated_at_trigger_is_installed_everywhere() -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'touch_updated_at' "
                "AND NOT tgisinternal"
            )
        )
        assert rows.scalar_one() == EXPECTED_TABLE_COUNT
