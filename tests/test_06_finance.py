"""6. Молиявий ҳисоб-китоблар тўғри."""
from datetime import timedelta

from app.database.models import utcnow
from app.services import books, finance, orders


async def test_book_cost_formula(env):
    async with env.sf() as s:
        b = await books.create_book(s, "Тест", print_run=2000, price=45000)
        await finance.add_expense(s, 18_000_000, "printing", book_id=b.id)
        await finance.add_expense(s, 2_000_000, "paper", book_id=b.id)
        await finance.add_expense(s, 4_000_000, "royalty", book_id=b.id, is_estimate=True)
        await finance.add_expense(s, 999_999, "rent")  # not tied to the book
        c = await finance.book_cost(s, b)
    assert c.actual_total == 20_000_000
    assert c.estimate_total == 4_000_000
    assert c.unit_cost == 12_000  # (20 000 000 + 4 000 000) / 2000
    assert c.unit_cost_actual_only == 10_000
    assert c.margin_per_copy == 33_000
    assert c.has_estimates


async def test_order_totals_payments_and_report(env):
    async with env.sf() as s:
        b = await books.create_book(s, "Тест", price=50000)
        cl = await orders.create_client(s, "Дўкон")
        o, _ = await orders.create_order(s, cl.id, b.id, 30, 50000)  # 1 500 000
        await orders.apply_discount(s, o, 10)  # 1 350 000
        assert o.total == 1_350_000
        await orders.record_payment(s, o, 1_000_000)
        assert o.payment_status.value == "partial" and o.debt == 350_000
        await finance.add_expense(s, 400_000, "Транспорт харажати")  # categorized
        await finance.add_expense(s, 100_000, "marketing", is_estimate=True)
        await finance.add_revenue(s, 200_000, description="ярмарка")
        r = await finance.period_report(s, utcnow() - timedelta(days=1), utcnow() + timedelta(seconds=5))
    assert r.revenue == 1_200_000
    assert r.expenses_actual == 400_000 and r.by_category == {"logistics": 400_000}
    assert r.expenses_estimate == 100_000
    assert r.profit_actual == 800_000
    assert r.profit_with_estimates == 700_000
    assert r.receivables == 350_000
    assert r.orders_count == 1 and r.orders_total == 1_350_000


async def test_overpayment_marks_paid_without_negative_debt(env):
    async with env.sf() as s:
        b = await books.create_book(s, "Тест")
        cl = await orders.create_client(s, "Мактаб")
        o, _ = await orders.create_order(s, cl.id, b.id, 2, 1000)
        await orders.record_payment(s, o, 5000)
    assert o.payment_status.value == "paid" and o.debt == 0


def test_money_format():
    assert finance.fmt_money(1234567) == "1 234 567 сўм"
