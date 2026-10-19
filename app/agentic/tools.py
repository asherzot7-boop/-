"""Typed tools the agent may call.

Every tool declares:
- an argument schema (pydantic) — the model's JSON is validated against it;
- the minimum role of the person on whose behalf the workflow runs;
- whether it only reads data, writes data, or needs a director's approval.

Tools reuse the existing services, so the business rules (stages, the
approval-only path to printing, cost formulas) stay in one place.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.editor import fact_flags, make_diff, rule_fixes
from app.agents.methodist import age_fit, default_plan, readability
from app.database.models import ApprovalKind, BookStage, Role, User, utcnow
from app.services import approvals, books, finance, orders
from app.services.books import STAGE_NAMES
from app.services.finance import fmt_money

Risk = Literal["read", "write", "approval"]


@dataclass
class ToolContext:
    session: AsyncSession
    user: User
    workflow_id: int


@dataclass
class ToolOutput:
    data: dict[str, Any]
    pause_for_approval: int | None = None  # approval id; the workflow waits for it


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[ToolContext, Any], Awaitable[ToolOutput]]
    min_role: Role = Role.STAFF
    risk: Risk = "read"
    tags: list[str] = field(default_factory=list)

    def spec(self) -> dict:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": schema}}


# ---------------------------------------------------------------- arguments
class NoArgs(BaseModel):
    pass


class BookRef(BaseModel):
    book_id: int = Field(gt=0, description="Китоб ID рақами")


class CreateBookArgs(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    kind: Literal["book", "textbook", "manual"] = "book"
    target_audience: str | None = Field(default=None, max_length=200)
    print_run: int | None = Field(default=None, gt=0, le=1_000_000)


class TextArgs(BaseModel):
    text: str = Field(default="", max_length=20000, description="Текшириладиган матн")


class PlanArgs(BaseModel):
    text: str = Field(default="", max_length=20000)
    grade: int | None = Field(default=None, ge=1, le=11)


class DiscountArgs(BaseModel):
    order_id: int = Field(gt=0)
    percent: float = Field(gt=0, le=50)


class PaymentArgs(BaseModel):
    amount: int = Field(gt=0, le=10_000_000_000)
    purpose: str = Field(min_length=3, max_length=200)


# ---------------------------------------------------------------- handlers
async def _list_books(ctx: ToolContext, a: NoArgs) -> ToolOutput:
    rows = await books.list_books(ctx.session)
    items = [{"id": b.id, "title": b.title, "stage": STAGE_NAMES[b.stage], "print_run": b.print_run} for b in rows]
    return ToolOutput({"books": items, "summary": f"{len(items)} та китоб"})


async def _create_book(ctx: ToolContext, a: CreateBookArgs) -> ToolOutput:
    b = await books.create_book(ctx.session, a.title, kind=a.kind, target_audience=a.target_audience, print_run=a.print_run)
    return ToolOutput({"book_id": b.id, "title": b.title, "stage": STAGE_NAMES[b.stage], "summary": f"#{b.id} «{b.title}» очилди (қўлёзма)"})


async def _review_text(ctx: ToolContext, a: TextArgs) -> ToolOutput:
    if not a.text.strip():
        return ToolOutput({"error": "Матн берилмаган", "summary": "Матн берилмаган — муҳаррир кутиб турибди"})
    fixed, notes = rule_fixes(a.text)
    facts = fact_flags(a.text)
    return ToolOutput({
        "original": a.text, "suggested": fixed, "diff": make_diff(a.text, fixed), "notes": notes, "facts_to_verify": facts,
        "summary": f"{len(notes)} та изоҳ, {len(facts)} та текширилиши керак бўлган жой; асл матн ўзгармади",
    })


async def _plan_textbook(ctx: ToolContext, a: PlanArgs) -> ToolOutput:
    stats = readability(a.text) if a.text else {"sentences": 0, "words": 0, "avg_sentence_words": 0, "avg_word_letters": 0}
    plan = default_plan("", a.grade)
    return ToolOutput({"grade": a.grade, "readability": stats, "age_fit": age_fit(stats, a.grade), "plan": plan,
                       "summary": f"{len(plan)} бобли режа таклиф қилинди; {age_fit(stats, a.grade)}"})


async def _book_cost(ctx: ToolContext, a: BookRef) -> ToolOutput:
    b = await books.get_book(ctx.session, a.book_id)
    if b is None:
        return ToolOutput({"error": f"#{a.book_id} китоб топилмади", "summary": "китоб топилмади"})
    c = await finance.book_cost(ctx.session, b)
    unit = fmt_money(c.unit_cost) + " / дона" if c.unit_cost is not None else "тираж йўқ, ҳисоблаб бўлмайди"
    return ToolOutput({"book_id": b.id, "actual_total": c.actual_total, "estimate_total": c.estimate_total, "unit_cost": c.unit_cost,
                       "formula": c.formula, "summary": f"«{b.title}» таннархи: {unit}"})


async def _sales_report(ctx: ToolContext, a: NoArgs) -> ToolOutput:
    rows = await orders.list_orders(ctx.session, limit=500)
    debtors = [o for o in rows if o.debt > 0]
    return ToolOutput({"orders": len(rows), "total": sum(o.total for o in rows), "debt": sum(o.debt for o in debtors),
                       "debtors": [{"order_id": o.id, "client": o.client.name, "debt": o.debt} for o in debtors[:20]],
                       "summary": f"{len(rows)} та буюртма, қарз {fmt_money(sum(o.debt for o in debtors))}"})


async def _finance_report(ctx: ToolContext, a: NoArgs) -> ToolOutput:
    r = await finance.period_report(ctx.session, utcnow() - timedelta(days=30))
    return ToolOutput({"revenue": r.revenue, "expenses_actual": r.expenses_actual, "expenses_estimate": r.expenses_estimate,
                       "profit_actual": r.profit_actual, "receivables": r.receivables,
                       "summary": f"30 кун: тушум {fmt_money(r.revenue)}, ҳақиқий харажат {fmt_money(r.expenses_actual)}, фойда {fmt_money(r.profit_actual)}"})


async def _send_to_print(ctx: ToolContext, a: BookRef) -> ToolOutput:
    b = await books.get_book(ctx.session, a.book_id)
    if b is None:
        return ToolOutput({"error": f"#{a.book_id} китоб топилмади", "summary": "китоб топилмади"})
    if b.stage in (BookStage.PRINTING, BookStage.READY):
        return ToolOutput({"book_id": b.id, "summary": f"«{b.title}» аллақачон {STAGE_NAMES[b.stage]} босқичида"})
    ap, _ = await approvals.request_approval(
        ctx.session, ApprovalKind.FINAL_PRINT, f"«{b.title}» китобини босмахонага юбориш",
        {"book_id": b.id, "workflow_id": ctx.workflow_id}, requested_by=ctx.user, agent="agentic",
        idempotency_key=f"wf:{ctx.workflow_id}:print:{b.id}",
    )
    return ToolOutput({"book_id": b.id, "approval_id": ap.id, "summary": f"тасдиқ сўрови #{ap.id} яратилди"}, pause_for_approval=ap.id)


async def _request_discount(ctx: ToolContext, a: DiscountArgs) -> ToolOutput:
    o = await orders.get_order(ctx.session, a.order_id)
    if o is None:
        return ToolOutput({"error": f"#{a.order_id} буюртма топилмади", "summary": "буюртма топилмади"})
    ap, _ = await approvals.request_approval(
        ctx.session, ApprovalKind.DISCOUNT, f"#{o.id} буюртмага {a.percent:g}% чегирма ({o.client.name})",
        {"order_id": o.id, "percent": a.percent, "total_before": o.total, "workflow_id": ctx.workflow_id},
        requested_by=ctx.user, agent="agentic", idempotency_key=f"wf:{ctx.workflow_id}:discount:{o.id}",
    )
    return ToolOutput({"order_id": o.id, "approval_id": ap.id, "summary": f"чегирма учун тасдиқ сўрови #{ap.id}"}, pause_for_approval=ap.id)


async def _request_payment(ctx: ToolContext, a: PaymentArgs) -> ToolOutput:
    ap, _ = await approvals.request_approval(
        ctx.session, ApprovalKind.PAYMENT, f"Тўлов: {fmt_money(a.amount)} — {a.purpose}",
        {"amount": a.amount, "category": finance.categorize(a.purpose), "recipient": a.purpose, "workflow_id": ctx.workflow_id},
        requested_by=ctx.user, agent="agentic", idempotency_key=f"wf:{ctx.workflow_id}:payment:{a.amount}",
    )
    return ToolOutput({"approval_id": ap.id, "summary": f"тўлов сўрови #{ap.id}; тизим пул ўтказмайди"}, pause_for_approval=ap.id)


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("list_books", "Китоблар реестри ва уларнинг босқичлари", NoArgs, _list_books, Role.VIEWER, "read"),
    Tool("create_book", "Янги китоб ёки дарслик лойиҳасини очиш", CreateBookArgs, _create_book, Role.STAFF, "write"),
    Tool("review_text", "Матнни имло ва услуб бўйича текшириш. Фақат таклиф беради, асл матнни ўзгартирмайди", TextArgs, _review_text, Role.STAFF, "read"),
    Tool("plan_textbook", "Дарслик боблари режаси ва синфга мосликни баҳолаш", PlanArgs, _plan_textbook, Role.STAFF, "read"),
    Tool("book_cost", "Китоб таннархини формула билан ҳисоблаш", BookRef, _book_cost, Role.VIEWER, "read"),
    Tool("sales_report", "Буюртмалар, тушум ва қарздорлар", NoArgs, _sales_report, Role.VIEWER, "read"),
    Tool("finance_report", "Сўнгги 30 кун: тушум, харажат, фойда", NoArgs, _finance_report, Role.VIEWER, "read"),
    Tool("send_to_print", "Китобни босмахонага юбориш. Раҳбар тасдиғини сўрайди ва жараён шу қарорни кутади", BookRef, _send_to_print, Role.STAFF, "approval"),
    Tool("request_discount", "Буюртмага чегирма бериш. Раҳбар тасдиғини сўрайди", DiscountArgs, _request_discount, Role.STAFF, "approval"),
    Tool("request_payment", "Тўлов қилиш. Раҳбар тасдиғини сўрайди; тизим пул ўтказмайди", PaymentArgs, _request_payment, Role.STAFF, "approval"),
]}
