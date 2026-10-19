"""KPI and reports for the director. Every number comes from a database query."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AgentRun, Book, RunStatus, Task, TaskStatus, utcnow
from app.services import approvals, books, events, finance, settings_store, tasks
from app.services.finance import CATEGORY_NAMES, fmt_money

PERIODS = {"day": "кунлик", "week": "ҳафталик", "month": "ойлик"}
PERIOD_ALIASES = {
    "day": "day", "kun": "day", "кун": "day", "бугун": "day", "today": "day", "кунлик": "day",
    "week": "week", "hafta": "week", "ҳафта": "week", "ҳафталик": "week",
    "month": "month", "oy": "month", "ой": "month", "ойлик": "month",
}


def period_bounds(period: str, tz: ZoneInfo, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Start of the local day / ISO week / month up to now, both as naive UTC."""
    local = (now.replace(tzinfo=ZoneInfo("UTC")) if now and now.tzinfo is None else (now or datetime.now(ZoneInfo("UTC")))).astimezone(tz)
    day_start = datetime.combine(local.date(), time(0), tzinfo=tz)
    if period == "week":
        start = day_start - timedelta(days=local.weekday())
    elif period == "month":
        start = day_start.replace(day=1)
    else:
        start = day_start
    to_utc = lambda d: d.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return to_utc(start), to_utc(local)


@dataclass
class AgentStats:
    agent: str
    runs: int
    errors: int
    avg_ms: float
    last_run: datetime | None


@dataclass
class KPI:
    period: str
    start: datetime
    end: datetime
    books_total: int
    books_by_stage: dict[str, int]
    tasks_open: int
    tasks_overdue: int
    tasks_failed: int
    approvals_pending: int
    orders_count: int
    orders_total: int
    revenue: int
    expenses_actual: int
    expenses_estimate: int
    profit_actual: int
    profit_with_estimates: int
    receivables: int
    stock_total: int
    agents: list[AgentStats] = field(default_factory=list)
    agent_errors: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        for a in d["agents"]:
            a["last_run"] = a["last_run"].isoformat() if a["last_run"] else None
        return d


async def agent_stats(session: AsyncSession, since: datetime | None = None) -> list[AgentStats]:
    q = select(
        AgentRun.agent,
        func.count(),
        func.sum(case((AgentRun.status == RunStatus.ERROR, 1), else_=0)),
        func.avg(AgentRun.duration_ms),
        func.max(AgentRun.created_at),
    ).group_by(AgentRun.agent)
    if since:
        q = q.where(AgentRun.created_at >= since)
    out = []
    for agent, runs, errors, avg_ms, last in await session.execute(q):
        out.append(AgentStats(agent, int(runs), int(errors or 0), float(avg_ms or 0), last))
    return out


async def build_kpi(session: AsyncSession, period: str, tz: ZoneInfo, now: datetime | None = None) -> KPI:
    start, end = period_bounds(period, tz, now)
    by_stage = await books.count_by_stage(session)
    fin = await finance.period_report(session, start, end + timedelta(seconds=1))
    stats = await agent_stats(session, start)
    open_count = await session.scalar(select(func.count()).select_from(Task).where(Task.status.in_(tasks.OPEN_STATUSES), Task.parent_id.is_(None)))
    failed = await session.scalar(select(func.count()).select_from(Task).where(Task.status == TaskStatus.FAILED, Task.updated_at >= start))
    return KPI(
        period=period, start=start, end=end,
        books_total=int(await session.scalar(select(func.count()).select_from(Book)) or 0),
        books_by_stage={s.value: n for s, n in by_stage.items()},
        tasks_open=int(open_count or 0),
        tasks_overdue=len(await tasks.overdue_tasks(session, end)),
        tasks_failed=int(failed or 0),
        approvals_pending=len(await approvals.pending(session)),
        orders_count=fin.orders_count, orders_total=fin.orders_total,
        revenue=fin.revenue, expenses_actual=fin.expenses_actual, expenses_estimate=fin.expenses_estimate,
        profit_actual=fin.profit_actual, profit_with_estimates=fin.profit_with_estimates,
        receivables=fin.receivables, stock_total=await books.stock_total(session),
        agents=stats, agent_errors=sum(a.errors for a in stats),
    )


def _local(dt: datetime, tz: ZoneInfo) -> str:
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%d.%m.%Y %H:%M")


def format_kpi(k: KPI, tz: ZoneInfo) -> str:
    stages = "\n".join(f"  • {books.STAGE_NAMES[books.BookStage(s)]}: {n}" for s, n in k.books_by_stage.items())
    lines = [
        f"📊 {PERIODS[k.period].capitalize()} ҳисобот",
        f"Давр: {_local(k.start, tz)} — {_local(k.end, tz)}",
        "",
        f"📚 Китоб лойиҳалари: {k.books_total}",
        stages,
        "",
        f"📝 Очиқ вазифалар: {k.tasks_open}",
        f"⏰ Кечиккан: {k.tasks_overdue}",
        f"❌ Бажарилмаган (хато): {k.tasks_failed}",
        f"✋ Тасдиқ кутмоқда: {k.approvals_pending}",
        "",
        f"🧾 Буюртмалар: {k.orders_count} та, {fmt_money(k.orders_total)}",
        f"💰 Тушум: {fmt_money(k.revenue)}",
        f"💸 Харажат (ҳақиқий): {fmt_money(k.expenses_actual)}",
    ]
    if k.expenses_estimate:
        lines.append(f"   + тахминий харажат: {fmt_money(k.expenses_estimate)}")
    lines += [
        f"📈 Фойда (ҳақиқий) = тушум − харажат = {fmt_money(k.profit_actual)}",
    ]
    if k.expenses_estimate:
        lines.append(f"📉 Фойда (тахминийлар билан): {fmt_money(k.profit_with_estimates)}")
    lines += [
        f"🏦 Мижозлар қарзи: {fmt_money(k.receivables)}",
        f"📦 Омбордаги китоблар: {k.stock_total} дона",
        "",
        f"🤖 Агентлар: {sum(a.runs for a in k.agents)} марта ишлади, {k.agent_errors} хато",
    ]
    return "\n".join(lines)


def format_finance(r: finance.FinanceReport, tz: ZoneInfo, title: str) -> str:
    lines = [
        f"💼 {title}",
        f"Давр: {_local(r.start, tz)} — {_local(r.end, tz)}",
        f"Тушум: {fmt_money(r.revenue)}",
        f"Харажат (ҳақиқий): {fmt_money(r.expenses_actual)}",
    ]
    for cat, total in sorted(r.by_category.items(), key=lambda x: -x[1]):
        lines.append(f"  • {CATEGORY_NAMES.get(cat, cat)}: {fmt_money(total)}")
    if r.expenses_estimate:
        lines.append(f"Тахминий харажат (ҳали ҳақиқий эмас): {fmt_money(r.expenses_estimate)}")
    lines.append(f"Фойда = {fmt_money(r.revenue)} − {fmt_money(r.expenses_actual)} = {fmt_money(r.profit_actual)}")
    if r.expenses_estimate:
        lines.append(f"Тахминийлар билан фойда: {fmt_money(r.profit_with_estimates)}")
    lines.append(f"Қарздорлик (тўланмаган буюртмалар): {fmt_money(r.receivables)}")
    return "\n".join(lines)


# Automation hooks: keep stored snapshots fresh when data changes.
async def _snapshot_sales(session: AsyncSession, payload: dict) -> None:
    start = utcnow() - timedelta(days=30)
    r = await finance.period_report(session, start)
    await settings_store.set_setting(session, settings_store.SALES_SNAPSHOT, {
        "updated_at": utcnow().isoformat(), "orders_30d": r.orders_count, "orders_total_30d": r.orders_total,
        "revenue_30d": r.revenue, "receivables": r.receivables,
    })


async def _snapshot_finance(session: AsyncSession, payload: dict) -> None:
    start = utcnow() - timedelta(days=30)
    r = await finance.period_report(session, start)
    await settings_store.set_setting(session, settings_store.FINANCE_SNAPSHOT, {
        "updated_at": utcnow().isoformat(), "revenue_30d": r.revenue, "expenses_30d": r.expenses_actual,
        "estimates_30d": r.expenses_estimate, "profit_30d": r.profit_actual,
    })


def register_event_handlers() -> None:
    events.subscribe("order_changed", _snapshot_sales)
    events.subscribe("order_changed", _snapshot_finance)
    events.subscribe("expense_added", _snapshot_finance)
    events.subscribe("revenue_added", _snapshot_finance)
