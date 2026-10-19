"""Human-in-the-loop approvals.

Payments, contracts, mass messages, discounts and the final "send to print"
decision are never executed directly. An agent or a staff member creates an
Approval; only a director can decide it; the decision is written to
audit_logs with the user ID and time; only an APPROVED request runs its
executor, exactly once.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Approval, ApprovalKind, ApprovalStatus, BookStage, Role, Task, TaskStatus, User, utcnow
from app.security import audit
from app.security.permissions import AccessDenied, has_role

KIND_NAMES = {
    ApprovalKind.PAYMENT: "тўлов",
    ApprovalKind.CONTRACT: "шартнома",
    ApprovalKind.MASS_MESSAGE: "оммавий хабар",
    ApprovalKind.DISCOUNT: "чегирма",
    ApprovalKind.FINAL_PRINT: "нашрга якуний тасдиқ",
}

Executor = Callable[[AsyncSession, Approval], Awaitable[str]]


async def _exec_final_print(session: AsyncSession, approval: Approval) -> str:
    from app.services import books

    book = await books.get_book(session, int(approval.payload["book_id"]))
    if book is None:
        return "Китоб топилмади."
    await books.set_stage(session, book, BookStage.PRINTING, via_approval=True)
    return f"«{book.title}» босмахона босқичига ўтказилди. Файлни босмахонага ходим юборади."


async def _exec_discount(session: AsyncSession, approval: Approval) -> str:
    from app.services import orders

    order = await orders.get_order(session, int(approval.payload["order_id"]))
    if order is None:
        return "Буюртма топилмади."
    await orders.apply_discount(session, order, float(approval.payload["percent"]))
    return f"#{order.id} буюртмага {approval.payload['percent']}% чегирма қўлланди."


async def _exec_payment(session: AsyncSession, approval: Approval) -> str:
    from app.services import finance

    p = approval.payload
    await finance.add_expense(
        session, int(p["amount"]), p.get("category", "other"), book_id=p.get("book_id"),
        description=f"Тасдиқланган тўлов: {p.get('recipient', '')}".strip(),
        idempotency_key=f"approval:{approval.id}",
    )
    return "Тўлов тасдиқланди ва харажат сифатида қайд этилди. Банк тўловини масъул ходим ўзи амалга оширади — тизим пул ўтказмайди."


async def _exec_contract(session: AsyncSession, approval: Approval) -> str:
    return "Шартнома тасдиқланди. Имзолашни масъул ходим амалга оширади."


async def _exec_mass_message(session: AsyncSession, approval: Approval) -> str:
    return "Хабар матни тасдиқланди. Юборишни масъул ходим амалга оширади."


EXECUTORS: dict[ApprovalKind, Executor] = {
    ApprovalKind.FINAL_PRINT: _exec_final_print,
    ApprovalKind.DISCOUNT: _exec_discount,
    ApprovalKind.PAYMENT: _exec_payment,
    ApprovalKind.CONTRACT: _exec_contract,
    ApprovalKind.MASS_MESSAGE: _exec_mass_message,
}


async def request_approval(
    session: AsyncSession,
    kind: ApprovalKind,
    title: str,
    payload: dict,
    requested_by: User | None = None,
    agent: str | None = None,
    task: Task | None = None,
    idempotency_key: str | None = None,
) -> tuple[Approval, bool]:
    if idempotency_key:
        existing = await session.scalar(select(Approval).where(Approval.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
    approval = Approval(
        kind=kind, title=title[:300], payload=payload, requested_by_agent=agent,
        requested_by_id=requested_by.id if requested_by else None, task_id=task.id if task else None,
        idempotency_key=idempotency_key,
    )
    session.add(approval)
    await session.flush()
    if task is not None:
        task.status = TaskStatus.WAITING_APPROVAL
    await audit.log_action(session, "approval.requested", requested_by, "approval", approval.id, {"kind": kind.value, "agent": agent, "title": title})
    return approval, True


async def pending(session: AsyncSession) -> list[Approval]:
    return list(await session.scalars(select(Approval).where(Approval.status == ApprovalStatus.PENDING).order_by(Approval.id)))


class AlreadyDecided(Exception):
    def __init__(self, approval: Approval) -> None:
        super().__init__(f"#{approval.id} аллақачон кўриб чиқилган: {approval.status.value}")
        self.approval = approval


async def decide(session: AsyncSession, approval_id: int, user: User, approve: bool, note: str | None = None) -> Approval:
    if not has_role(user, Role.DIRECTOR):
        await audit.log_action(session, "approval.denied", user, "approval", approval_id, {"reason": "not director"})
        raise AccessDenied("Фақат раҳбар тасдиқлай олади.")
    approval = await session.get(Approval, approval_id, with_for_update=True)
    if approval is None:
        raise LookupError(f"#{approval_id} тасдиқ сўрови топилмади.")
    if approval.status != ApprovalStatus.PENDING:
        raise AlreadyDecided(approval)

    approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    approval.decided_by_id = user.id
    approval.decided_at = utcnow()
    approval.decision_note = note
    if approve:
        approval.execution_result = await EXECUTORS[approval.kind](session, approval)
    else:
        approval.execution_result = "Рад этилди. Амал бажарилмади."

    task = await session.get(Task, approval.task_id) if approval.task_id else None
    if task is not None and task.status == TaskStatus.WAITING_APPROVAL:
        task.status = TaskStatus.DONE if approve else TaskStatus.CANCELLED
        task.result = approval.execution_result

    await audit.log_action(
        session, "approval.decided", user, "approval", approval.id,
        {"decision": approval.status.value, "kind": approval.kind.value, "decided_at": approval.decided_at.isoformat(), "note": note},
    )
    await session.flush()
    return approval
