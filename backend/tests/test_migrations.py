import asyncio
import io

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import Base


def render_sql(config: Config, revision_range: str, *, downgrade: bool = False) -> str:
    buffer = io.StringIO()
    config.output_buffer = buffer
    if downgrade:
        command.downgrade(config, revision_range, sql=True)
    else:
        command.upgrade(config, revision_range, sql=True)
    return buffer.getvalue()


def test_upgrade_renders_offline(alembic_config: Config) -> None:
    sql = render_sql(alembic_config, "head")

    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table} " in sql


def test_downgrade_renders_offline(alembic_config: Config) -> None:
    sql = render_sql(alembic_config, "head:base", downgrade=True)

    for table in Base.metadata.tables:
        assert f"DROP TABLE {table};" in sql


def _schema_diff(connection: Connection) -> list[object]:
    return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))


def _table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names()) - {"alembic_version"}


async def _inspect_database(url: str) -> tuple[list[object], set[str]]:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        diff = await connection.run_sync(_schema_diff)
        tables = await connection.run_sync(_table_names)
    await engine.dispose()
    return diff, tables


@pytest.mark.integration
def test_migrations_match_models_and_round_trip(
    alembic_config: Config, test_database_url: str
) -> None:
    # A sync test on purpose: Alembic's env.py calls asyncio.run(), which cannot run inside
    # pytest-asyncio's already-running event loop.
    alembic_config.set_main_option("sqlalchemy.url", test_database_url)
    command.downgrade(alembic_config, "base")

    command.upgrade(alembic_config, "head")
    diff, tables = asyncio.run(_inspect_database(test_database_url))
    assert diff == [], f"Models and migrations disagree: {diff}"
    assert tables == set(Base.metadata.tables)

    command.downgrade(alembic_config, "base")
    _, tables = asyncio.run(_inspect_database(test_database_url))
    assert tables == set()

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
