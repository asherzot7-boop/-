"""Income, expenses, book cost and financial reports.

Every figure is computed from database rows with explicit formulas. Expenses
marked is_estimate=True are reported separately and never mixed into the
"actual" figures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Book, Expense, Order, OrderStatus, Revenue, utcnow
from app.services import events

EXPENSE_CATEGORIES = {
    "босма": "printing", "printing": "printing",
    "қоғоз": "paper", "qog'oz": "paper", "paper": "paper",
    "муаллиф": "royalty", "royalty": "royalty", "гонорар": "royalty",
    "дизайн": "design", "design": "design",
    "таҳрир": "editing", "editing": "editing",
    "ижара": "rent", "rent": "rent",
    "маош": "salary", "salary": "salary",
    "транспорт": "logistics", "logistics": "logistics",
    "реклама": "marketing", "marketing": "marketing",
}
CATEGORY_NAMES = {
    "printing": "босма", "paper": "қоғоз", "royalty": "муаллиф гонорари", "design": "дизайн", "editing": "таҳрир",
    "rent": "ижара", "salary": "маош", "logistics": "транспорт", "marketing": "реклама", "other": "бошқа",
}


def categorize(text: str) -> str:
    low = (text or "").lower()
    for word, cat in EXPENSE_CATEGORIES.items():
        if word in low:
            return cat
    return "other"


def fmt_money(amount: int | float) -> str:
    return f"{round(amount):,}".replace(",", " ") + " сўм"


async def add_expense(
    session: AsyncSession,
    amount: int,
    category: str,
    book_id: int | None = None,
    description: str | None = None,
    is_estimate: bool = False,
    idempotency_key: str | None = None,
) -> tuple[Expense, bool]:
    if amount <= 0:
        raise ValueError("Сумма мусбат бўлиши керак.")
    if idempotency_key:
        existing = await session.scalar(select(Expense).where(Expense.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
    cat = category if category in CATEGORY_NAMES else categorize(category)
    expense = Expense(amount=amount, category=cat, book_id=book_id, description=description, is_estimate=is_estimate, idempotency_key=idempotency_key)
    session.add(expense)
    await session.flush()
    await events.emit(session, "expense_added", {"expense_id": expense.id})
    return expense, True


async def add_revenue(
    session: AsyncSession, amount: int, source: str = "other", book_id: int | None = None,
    description: str | None = None, idempotency_key: str | None = None,
) -> tuple[Revenue, bool]:
    if amount <= 0:
        raise ValueError("Сумма мусбат бўлиши керак.")
    if idempotency_key:
        existing = await session.scalar(select(Revenue).where(Revenue.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
    revenue = Revenue(amount=amount, source=source, book_id=book_id, description=description, idempotency_key=idempotency_key)
    session.add(revenue)
    await session.flush()
    await events.emit(session, "revenue_added", {"revenue_id": revenue.id})
    return revenue, True


@dataclass
class BookCost:
    book_id: int
    title: str
    print_run: int | None
    actual_total: int
    estimate_total: int
    unit_cost: float | None
    unit_cost_actual_only: float | None
    price: int | None
    margin_per_copy: float | None
    formula: str
    has_estimates: bool


async def book_cost(session: AsyncSession, book: Book) -> BookCost:
    rows = await session.execute(
        select(Expense.is_estimate, func.coalesce(func.sum(Expense.amount), 0)).where(Expense.book_id == book.id).group_by(Expense.is_estimate)
    )
    sums = {bool(k): int(v) for k, v in rows}
    actual, estimate = sums.get(False, 0), sums.get(True, 0)
    unit = unit_actual = margin = None
    if book.print_run:
        unit = (actual + estimate) / book.print_run
        unit_actual = actual / book.print_run
        if book.price is not None:
            margin = book.price - unit
    formula = "таннарх = (ҳақиқий харажат + тахминий харажат) / тираж"
    if book.print_run:
        formula += f" = ({actual} + {estimate}) / {book.print_run}"
    return BookCost(book.id, book.title, book.print_run, actual, estimate, unit, unit_actual, book.price, margin, formula, estimate > 0)


@dataclass
class FinanceReport:
    start: datetime
    end: datetime
    revenue: int
    expenses_actual: int
    expenses_estimate: int
    profit_actual: int
    profit_with_estimates: int
    receivables: int
    by_category: dict[str, int] = field(default_factory=dict)
    orders_count: int = 0
    orders_total: int = 0


async def receivables(session: AsyncSession) -> int:
    orders = await session.scalars(select(Order).where(Order.status != OrderStatus.CANCELLED))
    return sum(o.debt for o in orders)


async def period_report(session: AsyncSession, start: datetime, end: datetime | None = None) -> FinanceReport:
    end = end or utcnow()
    revenue = int(await session.scalar(
        select(func.coalesce(func.sum(Revenue.amount), 0)).where(Revenue.received_at >= start, Revenue.received_at < end)
    ) or 0)
    rows = await session.execute(
        select(Expense.category, Expense.is_estimate, func.sum(Expense.amount))
        .where(Expense.spent_at >= start, Expense.spent_at < end)
        .group_by(Expense.category, Expense.is_estimate)
    )
    actual = estimate = 0
    by_cat: dict[str, int] = {}
    for cat, is_est, total in rows:
        total = int(total or 0)
        if is_est:
            estimate += total
        else:
            actual += total
            by_cat[cat] = by_cat.get(cat, 0) + total
    orders = list(await session.scalars(
        select(Order).where(Order.created_at >= start, Order.created_at < end, Order.status != OrderStatus.CANCELLED)
    ))
    return FinanceReport(
        start=start, end=end, revenue=revenue, expenses_actual=actual, expenses_estimate=estimate,
        profit_actual=revenue - actual, profit_with_estimates=revenue - actual - estimate,
        receivables=await receivables(session), by_category=by_cat,
        orders_count=len(orders), orders_total=sum(o.total for o in orders),
    )
