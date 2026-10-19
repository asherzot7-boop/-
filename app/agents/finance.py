"""Finance agent: categorise, cost per book, P&L, receivables.

Never makes bank payments. A payment request becomes an Approval.
"""
from __future__ import annotations

import re
from datetime import timedelta

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.textutil import extract_amount, extract_id
from app.database.models import ApprovalKind, utcnow
from app.services import approvals, books, finance
from app.services.finance import CATEGORY_NAMES, fmt_money

_COST = re.compile(r"таннарх|tannarx|себестоим", re.I)
_PAY = re.compile(r"тўла|тўлов|to'la|to'lov|toʻlov|ўтказ|o'tkaz", re.I)


class FinanceAgent(BaseAgent):
    name = "finance"
    title = "Молия"
    description = "Кирим-чиқим, таннарх, фойда ва қарздорлик ҳисоботи (формулалар билан)."

    async def handle(self, ctx: AgentContext) -> AgentResult:
        text = ctx.instruction
        if _COST.search(text):
            book_id = ctx.shared.get("book_id") or extract_id(text)
            book = await books.get_book(ctx.session, book_id) if book_id else None
            if book is None:
                return AgentResult(self.name, "Таннарх учун китоб рақамини ёзинг: «#3 китоб таннархи».", decision="китоб аниқланмади")
            c = await finance.book_cost(ctx.session, book)
            lines = [f"«{c.title}» таннархи:", f"• Ҳақиқий харажат: {fmt_money(c.actual_total)}", f"• Тахминий харажат: {fmt_money(c.estimate_total)}"]
            if c.unit_cost is None:
                lines.append("• Тираж киритилмаган — бир дона таннархини ҳисоблаб бўлмайди.")
            else:
                lines.append(f"• {c.formula} = {fmt_money(c.unit_cost)} / дона")
                if c.has_estimates:
                    lines.append(f"  (фақат ҳақиқий харажат бўйича: {fmt_money(c.unit_cost_actual_only)} / дона)")
                if c.margin_per_copy is not None:
                    lines.append(f"• Нарх {fmt_money(c.price)} − таннарх = {fmt_money(c.margin_per_copy)} / дона")
            return AgentResult(self.name, "\n".join(lines), data=c.__dict__, decision="таннарх ҳисобланди")

        if _PAY.search(text):
            amount = extract_amount(text)
            if not amount:
                return AgentResult(self.name, "Тўлов суммасини ёзинг.", decision="сумма йўқ")
            category = finance.categorize(text)
            approval, _ = await approvals.request_approval(
                ctx.session, ApprovalKind.PAYMENT, f"Тўлов: {fmt_money(amount)} ({CATEGORY_NAMES.get(category, category)})",
                {"amount": amount, "category": category, "recipient": text[:200], "book_id": ctx.shared.get("book_id")},
                requested_by=ctx.user, agent=self.name, task=ctx.task,
                idempotency_key=f"payment:task:{ctx.task.id}" if ctx.task else None,
            )
            return AgentResult(self.name, f"Тизим банк тўловини амалга оширмайди. Тўлов сўрови #{approval.id} раҳбар тасдиғига юборилди.", data={"approval_id": approval.id, "amount": amount}, decision="раҳбар тасдиғи сўралди", approval_ids=[approval.id])

        r = await finance.period_report(ctx.session, utcnow() - timedelta(days=30))
        lines = [
            "Молия (сўнгги 30 кун):",
            f"• Тушум: {fmt_money(r.revenue)}",
            f"• Харажат (ҳақиқий): {fmt_money(r.expenses_actual)}",
            f"• Фойда = тушум − ҳақиқий харажат = {fmt_money(r.profit_actual)}",
        ]
        if r.expenses_estimate:
            lines.append(f"• Тахминий харажатлар алоҳида: {fmt_money(r.expenses_estimate)} (фойда улар билан: {fmt_money(r.profit_with_estimates)})")
        lines.append(f"• Мижозлар қарзи: {fmt_money(r.receivables)}")
        return AgentResult(self.name, "\n".join(lines), data=r.__dict__, decision="ҳисобот тайёр")
