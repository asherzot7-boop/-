"""Sales agent: clients, orders, offers, follow-up plan.

Discounts and mass messages go through approvals; prices are never set by the agent.
"""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import func, select

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.llm import clean_untrusted
from app.agents.textutil import extract_id, extract_percent
from app.database.models import ApprovalKind, Client, Order, OrderStatus, utcnow
from app.services import approvals, orders
from app.services.finance import fmt_money

_DISCOUNT = re.compile(r"чегирма|chegirma|скидка", re.IGNORECASE)
_MASS = re.compile(r"оммавий|ҳаммага|hammaga|ommaviy|рассылка|барча мижоз", re.IGNORECASE)
_OFFER = re.compile(r"таклиф|taklif|хабар\s+мат|xabar", re.IGNORECASE)

SYSTEM = (
    "Сен нашриёт сотув менежерисан. Мижозга қисқа, хушмуомала таклиф хабарини ўзбек тилида ёз. "
    "Нарх ва чегирмани ўзингдан қўшма. Фақат хабар матнини қайтар."
)


class SalesAgent(BaseAgent):
    name = "sales"
    title = "Сотув"
    description = "Мижозлар, буюртмалар, таклиф матнлари, сотув ҳисоботи ва қайта алоқа режаси."

    async def handle(self, ctx: AgentContext) -> AgentResult:
        text = ctx.instruction
        if _DISCOUNT.search(text):
            order_id, percent = extract_id(text), extract_percent(text)
            if not order_id or percent is None:
                return AgentResult(self.name, "Чегирма учун буюртма рақами ва фоизни ёзинг: «#5 буюртмага 10% чегирма».", decision="маълумот етарли эмас")
            order = await orders.get_order(ctx.session, order_id)
            if order is None:
                return AgentResult(self.name, f"#{order_id} буюртма топилмади.", decision="буюртма йўқ")
            approval, _ = await approvals.request_approval(
                ctx.session, ApprovalKind.DISCOUNT, f"#{order.id} буюртмага {percent}% чегирма ({order.client.name})",
                {"order_id": order.id, "percent": percent, "total_before": order.total},
                requested_by=ctx.user, agent=self.name, task=ctx.task,
                idempotency_key=f"discount:task:{ctx.task.id}" if ctx.task else None,
            )
            return AgentResult(self.name, f"Чегирма мустақил белгиланмайди. Тасдиқ сўрови #{approval.id} раҳбарга юборилди.", data={"approval_id": approval.id}, decision="раҳбар тасдиғи сўралди", approval_ids=[approval.id])

        if _MASS.search(text):
            draft = await self._draft(text)
            n = await ctx.session.scalar(select(func.count()).select_from(Client))
            approval, _ = await approvals.request_approval(
                ctx.session, ApprovalKind.MASS_MESSAGE, f"Оммавий хабар ({n} мижоз)", {"text": draft, "audience": "all_clients", "count": n},
                requested_by=ctx.user, agent=self.name, task=ctx.task,
                idempotency_key=f"mass:task:{ctx.task.id}" if ctx.task else None,
            )
            return AgentResult(self.name, f"Хабар матни тайёр, лекин тасдиқсиз юборилмайди (сўров #{approval.id}).\n\n{draft}", data={"draft": draft, "approval_id": approval.id}, decision="раҳбар тасдиғи сўралди", approval_ids=[approval.id])

        if _OFFER.search(text):
            draft = await self._draft(text)
            return AgentResult(self.name, f"Таклиф матни (лойиҳа, юборилмади):\n\n{draft}", data={"draft": draft}, decision="матн тайёрланди", llm_used=self.llm.enabled)

        return await self._report(ctx)

    async def _draft(self, request: str) -> str:
        raw = clean_untrusted(await self.ask_llm(SYSTEM, request[:2000]))
        if raw:
            return raw[:2000]
        return (
            "Ассалому алайкум!\n"
            "Нашриётимизнинг янги нашрлари ҳақида маълум қилмоқчимиз. "
            "Мактаб ва дўконлар учун буюртмалар қабул қилинмоқда. "
            "Нарх ва етказиб бериш шартлари бўйича биз билан боғланинг.\n"
            "Ҳурмат билан, нашриёт жамоаси."
        )

    async def _report(self, ctx: AgentContext) -> AgentResult:
        since = utcnow() - timedelta(days=30)
        recent = list(await ctx.session.scalars(select(Order).where(Order.created_at >= since, Order.status != OrderStatus.CANCELLED)))
        all_orders = await orders.list_orders(ctx.session, limit=500)
        debtors = [o for o in all_orders if o.status != OrderStatus.CANCELLED and o.debt > 0]
        clients = await orders.list_clients(ctx.session)
        active_ids = {o.client_id for o in recent}
        follow_up = [c for c in clients if c.id not in active_ids]
        lines = [
            "Сотув ҳисоботи (сўнгги 30 кун):",
            f"• Буюртмалар: {len(recent)} та, {fmt_money(sum(o.total for o in recent))}",
            f"• Қарздор буюртмалар: {len(debtors)} та, {fmt_money(sum(o.debt for o in debtors))}",
        ]
        if debtors:
            lines += [f"  – #{o.id} {o.client.name}: {fmt_money(o.debt)}" for o in debtors[:5]]
        lines.append(f"• Қайта алоқа режаси: 30 кун ичида буюртма бермаган {len(follow_up)} мижоз")
        lines += [f"  – {c.name} {c.phone or ''}".rstrip() for c in follow_up[:5]]
        return AgentResult(
            self.name, "\n".join(lines),
            data={"orders_30d": len(recent), "debtors": [o.id for o in debtors], "follow_up": [c.id for c in follow_up]},
            decision="ҳисобот тайёр",
        )
