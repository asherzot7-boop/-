"""Async engine / session factory and Alembic migration runner."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[2]


def make_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite"):
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(url, pool_pre_ping=not url.startswith("sqlite"))


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def alembic_config(url: str):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "app" / "database" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


def run_migrations(url: str, revision: str = "head") -> None:
    """Apply migrations synchronously. Must be called outside a running event loop."""
    from alembic import command

    if url.startswith("sqlite"):
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(url), revision)
