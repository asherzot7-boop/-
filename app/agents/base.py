"""Base agent: common context, result type, and the journaling wrapper."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import LLMClient, MockLLM
from app.database.models import AgentRun, RunStatus, Task, User

log = logging.getLogger(__name__)


@dataclass
class AgentContext:
    session: AsyncSession
    instruction: str
    user: User | None = None
    task: Task | None = None
    previous: list[AgentResult] = field(default_factory=list)
    shared: dict[str, Any] = field(default_factory=dict)  # data passed between steps, e.g. book_id


@dataclass
class AgentResult:
    agent: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    decision: str | None = None
    approval_ids: list[int] = field(default_factory=list)
    llm_used: bool = False


class BaseAgent:
    name: str = "base"
    title: str = "Агент"
    description: str = ""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or MockLLM()

    async def handle(self, ctx: AgentContext) -> AgentResult:  # pragma: no cover - interface
        raise NotImplementedError

    async def ask_llm(self, system: str, user: str, json_mode: bool = False) -> str:
        """Call the LLM; on any failure return '' so the agent uses its fallback."""
        if not self.llm.enabled:
            return ""
        try:
            return await self.llm.complete(system, user, json_mode=json_mode)
        except Exception as exc:  # network, quota, timeout...
            log.warning("%s: LLM чақируви муваффақиятсиз (%s), қоида асосидаги режимга ўтилди", self.name, type(exc).__name__)
            return ""

    async def run(self, ctx: AgentContext) -> tuple[AgentResult | None, AgentRun]:
        """Run the agent and always journal the run. Never raises."""
        started = time.perf_counter()
        run = AgentRun(agent=self.name, task_id=ctx.task.id if ctx.task else None, input_text=ctx.instruction[:4000])
        try:
            async with ctx.session.begin_nested():
                result = await self.handle(ctx)
            run.status = RunStatus.SUCCESS
            run.output_text = result.summary[:8000]
            run.output_data = _jsonable(result.data)
            run.decision = (result.decision or "")[:300] or None
            run.llm_used = result.llm_used
        except Exception as exc:
            log.exception("Агент %s хатоси", self.name)
            result = None
            run.status = RunStatus.ERROR
            run.error = f"{type(exc).__name__}: {exc}"[:2000]
        run.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        ctx.session.add(run)
        await ctx.session.flush()
        return result, run


def _jsonable(data: Any) -> Any:
    if isinstance(data, dict):
        return {str(k): _jsonable(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_jsonable(v) for v in data]
    if isinstance(data, (str, int, float, bool)) or data is None:
        return data
    return str(data)
