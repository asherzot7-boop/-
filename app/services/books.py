"""Book registry and production stages."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Book, BookStage, InventoryItem

STAGE_ORDER = list(BookStage)
STAGE_NAMES = {
    BookStage.MANUSCRIPT: "қўлёзма",
    BookStage.EDITING: "таҳрир",
    BookStage.PROOFREADING: "корректура",
    BookStage.DESIGN: "дизайн",
    BookStage.LAYOUT: "макет",
    BookStage.PRINTING: "босмахона",
    BookStage.READY: "тайёр",
}
STAGE_BY_NAME = {v: k for k, v in STAGE_NAMES.items()} | {s.value: s for s in BookStage}


class ApprovalRequired(Exception):
    """Raised when an action needs a director's decision first."""


async def create_book(session: AsyncSession, title: str, kind: str = "book", **fields) -> Book:
    book = Book(title=title.strip()[:300], kind=kind, **fields)
    session.add(book)
    await session.flush()
    return book


async def get_book(session: AsyncSession, book_id: int) -> Book | None:
    return await session.get(Book, book_id)


async def list_books(session: AsyncSession) -> list[Book]:
    return list(await session.scalars(select(Book).order_by(Book.id)))


async def count_by_stage(session: AsyncSession) -> dict[BookStage, int]:
    rows = await session.execute(select(Book.stage, func.count()).group_by(Book.stage))
    counts = {stage: 0 for stage in BookStage}
    for stage, n in rows:
        counts[stage] = n
    return counts


async def set_stage(session: AsyncSession, book: Book, stage: BookStage, via_approval: bool = False) -> Book:
    """Move a book to a stage. Sending to the printing house needs an approved request."""
    if stage == BookStage.PRINTING and not via_approval:
        raise ApprovalRequired("Китобни босмахонага юбориш учун раҳбар тасдиғи керак.")
    book.stage = stage
    await session.flush()
    return book


async def add_stock(session: AsyncSession, book_id: int, quantity: int, location: str = "asosiy") -> InventoryItem:
    item = await session.scalar(select(InventoryItem).where(InventoryItem.book_id == book_id, InventoryItem.location == location))
    if item is None:
        item = InventoryItem(book_id=book_id, location=location, quantity=0)
        session.add(item)
    if item.quantity + quantity < 0:
        raise ValueError("Омборда етарли китоб йўқ.")
    item.quantity += quantity
    await session.flush()
    return item


async def stock_total(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.coalesce(func.sum(InventoryItem.quantity), 0))) or 0)


async def stock_by_book(session: AsyncSession) -> list[tuple[Book, int]]:
    rows = await session.execute(
        select(Book, func.sum(InventoryItem.quantity)).join(InventoryItem, InventoryItem.book_id == Book.id).group_by(Book.id).order_by(Book.id)
    )
    return [(b, int(q or 0)) for b, q in rows]
