"""9. База миграциялари тўғри ишлайди (синхрон тест: Alembic ўзи event loop очади)."""
import os
import sqlite3

import pytest

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.database.models import Base
from app.database.session import alembic_config, run_migrations

TABLES = {"users", "books", "tasks", "agent_runs", "clients", "orders", "expenses", "revenues", "inventory", "approvals", "audit_logs", "settings"}


def _tables(path) -> set[str]:
    with sqlite3.connect(path) as con:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}


def test_upgrade_downgrade_upgrade(tmp_path):
    path = tmp_path / "m.db"
    url = f"sqlite+aiosqlite:///{path}"
    run_migrations(url)
    assert TABLES <= _tables(path)
    assert "alembic_version" in _tables(path)

    command.downgrade(alembic_config(url), "base")
    assert not (TABLES & _tables(path))

    run_migrations(url)
    assert TABLES <= _tables(path)


def test_models_match_migrations(tmp_path):
    path = tmp_path / "m.db"
    run_migrations(f"sqlite+aiosqlite:///{path}")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    engine.dispose()
    assert diff == [], f"Моделлар ва миграция фарқ қилади: {diff}"


def test_every_table_has_id_and_timestamps():
    for name in TABLES:
        cols = Base.metadata.tables[name].columns
        assert {"id", "created_at", "updated_at"} <= set(cols.keys()), name


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"), reason="TEST_DATABASE_URL (PostgreSQL) берилмаган")
def test_postgres_upgrade_downgrade_upgrade():
    import asyncio

    from sqlalchemy import inspect, text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.environ["TEST_DATABASE_URL"]

    async def run(fn):
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            result = await conn.run_sync(fn)
        await eng.dispose()
        return result

    def reset(conn):
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    def tables(conn):
        return set(inspect(conn).get_table_names())

    asyncio.run(run(reset))
    run_migrations(url)
    assert TABLES <= asyncio.run(run(tables))
    command.downgrade(alembic_config(url), "base")
    assert not (TABLES & asyncio.run(run(tables)))
    run_migrations(url)
    assert TABLES <= asyncio.run(run(tables))
