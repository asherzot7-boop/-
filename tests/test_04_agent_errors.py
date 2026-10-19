"""4. Агент хатоси бутун ботни ишдан чиқармайди."""
from sqlalchemy import select

from app.agents.base import BaseAgent
from app.agents.coordinator import Coordinator
from app.agents.llm import MockLLM
from app.database.models import AgentRun, RunStatus, Task, TaskStatus
from tests.conftest import DIRECTOR, build_env, make_settings


class ExplodingAgent(BaseAgent):
    name = "editor"
    title = "Муҳаррир"

    async def handle(self, ctx):
        raise RuntimeError("model crashed")


async def test_agent_failure_is_isolated(tmp_path):
    coord = Coordinator(MockLLM())
    coord.agents["editor"] = ExplodingAgent()
    env = await build_env(make_settings(tmp_path), coordinator=coord)

    reply = await env.service.dispatch(DIRECTOR, "Янги дарслик лойиҳасини оч, муҳаррирга бер, кейин методистга текширтир", idempotency_key="x")
    assert "хато" in reply.text.lower()
    async with env.sf() as s:
        subs = {t.assigned_agent: t for t in (await s.scalars(select(Task).where(Task.parent_id.is_not(None)))).all()}
        runs = {r.agent: r for r in (await s.scalars(select(AgentRun))).all()}
    assert subs["editor"].status == TaskStatus.FAILED
    assert subs["production"].status == TaskStatus.DONE
    assert subs["methodist"].status == TaskStatus.DONE  # next step still ran
    assert runs["editor"].status == RunStatus.ERROR and "model crashed" in runs["editor"].error

    # The bot keeps answering other commands
    assert "Агентлар ҳолати" in (await env.service.dispatch(DIRECTOR, "/agents")).text
    assert "хато 1" in (await env.service.dispatch(DIRECTOR, "/agents")).text
    await env.engine.dispose()


class FailingLLM:
    enabled = True

    async def complete(self, *a, **kw):
        raise TimeoutError("openai timeout")


async def test_llm_outage_falls_back(tmp_path):
    env = await build_env(make_settings(tmp_path), coordinator=Coordinator(FailingLLM()))
    reply = await env.service.dispatch(DIRECTOR, "Муҳаррир текширсин, матн: бу  матн ,текширилади.", idempotency_key="y")
    assert "Муҳаррир" in reply.text and "✅" in reply.text
    await env.engine.dispose()


async def test_unexpected_command_error_returns_message(env, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(env.service, "cmd_books", boom)
    reply = await env.service.dispatch(DIRECTOR, "/books")
    assert "Ички хатолик" in reply.text
    assert "Бугун" in (await env.service.dispatch(DIRECTOR, "/today")).text
