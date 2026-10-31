"""What the agent may sell: products from a JSON file plus books with a price.

The agent never invents prices: every price in a reply comes from here.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Book
from app.salesbot.lang import normalize

log = logging.getLogger(__name__)


@dataclass
class Product:
    id: str
    name: str
    price: int  # so'm
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    book_id: int | None = None  # set -> a confirmed lead becomes an Order for this book
    in_stock: bool = True


def fmt_money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def load_file(path: str | Path | None) -> list[Product]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.error("Каталог файли ўқилмади (%s): %s", p, exc)
        return []
    out = []
    for item in raw.get("products", raw if isinstance(raw, list) else []):
        try:
            out.append(Product(
                id=str(item["id"]), name=str(item["name"]), price=int(item["price"]),
                description=str(item.get("description", "")), keywords=[str(k) for k in item.get("keywords", [])],
                book_id=item.get("book_id"), in_stock=bool(item.get("in_stock", True)),
            ))
        except (KeyError, TypeError, ValueError):
            log.warning("Каталогдаги нотўғри ёзув ўтказиб юборилди: %s", item)
    return out


async def load_catalog(session: AsyncSession, path: str | None, include_books: bool = True) -> list[Product]:
    products = load_file(path)
    if include_books:
        linked = {p.book_id for p in products if p.book_id}
        for b in await session.scalars(select(Book).where(Book.price > 0).order_by(Book.id)):
            if b.id not in linked:
                products.append(Product(id=f"book-{b.id}", name=b.title, price=int(b.price), book_id=b.id))
    return [p for p in products if p.in_stock]


def _stems(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", normalize(text))
    return {w.replace("'", "")[:5] for w in words if len(w) >= 3}


_STOP = {stem[:5] for stem in ("kitob", "kitap", "kniga", "narxi", "qancha", "kerak", "bormi", "salom", "assal", "alayk", "merha", "fiyat", "kadar",
                               "zdravs", "skolk", "stoit", "menga", "sizda", "bizga", "nechta", "tane", "adet")}


def match(products: list[Product], message: str) -> Product | None:
    """Best product for a free-text message, or None. Prefix stems cope with Uzbek/Turkish suffixes."""
    words = _stems(message) - _STOP
    if not words:
        return None
    best, best_score = None, 0.0
    for p in products:
        keys = (_stems(p.name) | {s for k in p.keywords for s in _stems(k)}) - _STOP
        if not keys:
            continue
        hit = len(words & keys)
        if hit == 0:
            continue
        score = hit / len(keys) + hit
        if score > best_score:
            best, best_score = p, score
    return best


def catalog_lines(products: list[Product], limit: int = 8) -> str:
    return "\n".join(f"• {p.name} — {fmt_money(p.price)}" for p in products[:limit])
