from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.agents.coordinator import Coordinator
from app.agents.llm import MockLLM
from app.bot.commands import CommandService
from app.config import Settings
from app.database.models import Base
from app.database.session import make_engine, make_session_factory
from app.security.permissions import sync_users
from app.services import reporting

DIRECTOR, STAFF, VIEWER, STRANGER = 100, 200, 300, 999


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(
        # Set TEST_DATABASE_URL=postgresql+asyncpg://... to run the suite on PostgreSQL.
        database_url=os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path}/test.db",
        director_ids=[DIRECTOR], staff_ids=[STAFF], viewer_ids=[VIEWER],
        bot_token=None, openai_api_key=None, api_token=None,
    )
    base.update(kw)
    return Settings(_env_file=None, **base)


async def build_env(settings: Settings, coordinator: Coordinator | None = None, create: bool = True, model=None) -> SimpleNamespace:
    reporting.register_event_handlers()
    engine = make_engine(settings.database_url)
    if create:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    sf = make_session_factory(engine)
    async with sf() as s:
        await sync_users(s, settings.director_ids, settings.staff_ids, settings.viewer_ids)
    coordinator = coordinator or Coordinator(MockLLM())
    from app.agentic.llm import RuleToolModel
    from app.agentic.runtime import AgentRuntime

    runtime = AgentRuntime(model or RuleToolModel())
    return SimpleNamespace(settings=settings, engine=engine, sf=sf, coordinator=coordinator, runtime=runtime,
                           service=CommandService(sf, coordinator, settings, runtime=runtime))


@pytest.fixture
async def env(tmp_path):
    e = await build_env(make_settings(tmp_path))
    yield e
    await e.engine.dispose()
