"""7. Ҳисоботлар реал база маълумотларидан тузилади."""
from datetime import timedelta

from app.database.models import BookStage, utcnow
from app.services import approvals, books, finance, orders, reporting, tasks
from app.database.models import ApprovalKind
from tests.conftest import DIRECTOR


async def test_empty_db_gives_zeros(env):
    async with env.sf() as s:
        k = await reporting.build_kpi(s, "day", env.settings.tz)
    assert (k.books_total, k.tasks_open, k.revenue, k.expenses_actual, k.stock_total, k.approvals_pending) == (0, 0, 0, 0, 0, 0)


async def test_kpi_matches_rows(env):
    async with env.sf() as s:
        b1 = await books.create_book(s, "A", print_run=100)
        b2 = await books.create_book(s, "B")
        await books.set_stage(s, b2, BookStage.DESIGN)
        await books.add_stock(s, b1.id, 70)
        await books.add_stock(s, b2.id, 30)
        cl = await orders.create_client(s, "C")
        o, _ = await orders.create_order(s, cl.id, b1.id, 10, 1000)
        await orders.record_payment(s, o, 4000)
        await finance.add_expense(s, 1500, "rent")
        await tasks.create_task(s, "очиқ", due_at=utcnow() - timedelta(hours=2))  # overdue
        await tasks.create_task(s, "очиқ 2")
        await approvals.request_approval(s, ApprovalKind.CONTRACT, "шартнома", {})
        await s.commit()
        k = await reporting.build_kpi(s, "month", env.settings.tz, utcnow() + timedelta(seconds=1))
    assert k.books_total == 2
    assert k.books_by_stage["manuscript"] == 1 and k.books_by_stage["design"] == 1
    assert k.stock_total == 100
    assert k.orders_count == 1 and k.orders_total == 10_000
    assert k.revenue == 4000 and k.expenses_actual == 1500 and k.profit_actual == 2500
    assert k.receivables == 6000
    assert k.tasks_open == 2 and k.tasks_overdue == 1
    assert k.approvals_pending == 1

    text = (await env.service.dispatch(DIRECTOR, "/report month")).text
    assert "Китоб лойиҳалари: 2" in text and "4 000 сўм" in text and "Омбордаги китоблар: 100" in text


async def test_order_and_expense_events_refresh_snapshots(env):
    from app.services import settings_store

    async with env.sf() as s:
        b = await books.create_book(s, "A")
        cl = await orders.create_client(s, "C")
        await orders.create_order(s, cl.id, b.id, 3, 1000)
        sales = await settings_store.get_setting(s, settings_store.SALES_SNAPSHOT)
        await finance.add_expense(s, 700, "rent")
        fin = await settings_store.get_setting(s, settings_store.FINANCE_SNAPSHOT)
    assert sales["orders_30d"] == 1 and sales["orders_total_30d"] == 3000
    assert fin["expenses_30d"] == 700
