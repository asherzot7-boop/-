"""5. Тасдиқ талаб қилинадиган амал инсон тасдиғисиз бажарилмайди."""
import pytest
from sqlalchemy import func, select

from app.database.models import Approval, ApprovalStatus, AuditLog, Book, BookStage, Expense, Order
from app.services import books, orders
from app.services.books import ApprovalRequired
from tests.conftest import DIRECTOR, STAFF


async def _book(env, **kw) -> int:
    async with env.sf() as s:
        b = await books.create_book(s, "Она тили 5-синф", kind="textbook", **kw)
        await s.commit()
        return b.id


async def test_print_needs_director(env):
    book_id = await _book(env)
    reply = await env.service.dispatch(STAFF, f"#{book_id} китобни босмахонага юбор", idempotency_key="p1")
    assert "тасдиқ" in reply.text.lower()
    async with env.sf() as s:
        assert (await s.get(Book, book_id)).stage == BookStage.MANUSCRIPT
        approval = await s.scalar(select(Approval))
    assert approval.status == ApprovalStatus.PENDING

    # Staff cannot decide
    assert "раҳбар" in (await env.service.decide(STAFF, approval.id, True)).text
    async with env.sf() as s:
        assert (await s.get(Book, book_id)).stage == BookStage.MANUSCRIPT
        assert await s.scalar(select(AuditLog).where(AuditLog.action == "approval.denied")) is not None

    # Director approves -> executed once, logged with user, time and decision
    assert "Тасдиқланди" in (await env.service.decide(DIRECTOR, approval.id, True)).text
    assert "аллақачон" in (await env.service.decide(DIRECTOR, approval.id, True)).text
    async with env.sf() as s:
        assert (await s.get(Book, book_id)).stage == BookStage.PRINTING
        log = await s.scalar(select(AuditLog).where(AuditLog.action == "approval.decided"))
    assert log.telegram_id == DIRECTOR and log.user_id is not None
    assert log.details["decision"] == "approved" and log.details["decided_at"]


async def test_stage_command_cannot_skip_approval(env):
    book_id = await _book(env)
    reply = await env.service.dispatch(DIRECTOR, f"/books stage {book_id} босмахона", idempotency_key="s1")
    assert "тасдиғини талаб" in reply.text
    async with env.sf() as s:
        assert (await s.get(Book, book_id)).stage == BookStage.MANUSCRIPT
        with pytest.raises(ApprovalRequired):
            await books.set_stage(s, await s.get(Book, book_id), BookStage.PRINTING)


async def test_rejected_discount_is_not_applied(env):
    book_id = await _book(env, price=40000)
    async with env.sf() as s:
        c = await orders.create_client(s, "12-мактаб")
        o, _ = await orders.create_order(s, c.id, book_id, 10, 40000)
        await s.commit()
    await env.service.dispatch(STAFF, f"#{o.id} буюртмага 15% чегирма бер", idempotency_key="d1")
    async with env.sf() as s:
        assert (await s.get(Order, o.id)).discount_percent == 0
        approval = await s.scalar(select(Approval))
    await env.service.decide(DIRECTOR, approval.id, False)
    async with env.sf() as s:
        assert (await s.get(Order, o.id)).discount_percent == 0

    await env.service.dispatch(STAFF, f"/orders discount {o.id} 10", idempotency_key="d2")
    async with env.sf() as s:
        a2 = await s.scalar(select(Approval).where(Approval.status == ApprovalStatus.PENDING))
    await env.service.decide(DIRECTOR, a2.id, True)
    async with env.sf() as s:
        order = await s.get(Order, o.id)
    assert order.discount_percent == 10 and order.total == 360000


async def test_payment_and_mass_message_wait_for_approval(env):
    await env.service.dispatch(STAFF, "Босмахонага 5 000 000 сўм тўловни амалга ошир", idempotency_key="pay")
    await env.service.dispatch(STAFF, "Барча мижозларга оммавий хабар юбор", idempotency_key="mass")
    async with env.sf() as s:
        kinds = sorted(a.kind.value for a in (await s.scalars(select(Approval))).all())
        assert await s.scalar(select(func.count()).select_from(Expense)) == 0
    assert kinds == ["mass_message", "payment"]
