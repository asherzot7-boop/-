"""What the agent may sell: products or services from a JSON file, plus books with a price.

The agent never invents prices: every price in a reply comes from here.
Names, descriptions, document lists and the "details" question can be
given per language ({"uz": ..., "tr": ..., "ru": ...}); Uzbek Cyrillic is
transliterated from "uz" automatically.
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
from app.salesbot.lang import normalize, to_cyrillic

log = logging.getLogger(__name__)

SOM = {"so'm", "som", "uzs", "сўм", "сум"}


def _localized(value, lang: str) -> str:
    """str or {"uz": ..., "tr": ..., "ru": ...} -> text in that language (falls back to uz, then any)."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(f"• {v}" for v in value)
    if not isinstance(value, dict):
        return to_cyrillic(str(value)) if lang == "uzc" else str(value)
    if lang == "uzc":
        if value.get("uzc"):
            return _localized(value["uzc"], "uz")
        return _localized(value.get("uz") or next(iter(value.values()), ""), "uzc")
    v = value.get(lang) or value.get("uz") or next(iter(value.values()), "")
    return _localized(v, "uz")


@dataclass
class Product:
    id: str
    name: str | dict
    price: int  # in `currency`; 0 -> price is agreed individually
    description: str | dict = ""
    keywords: list[str] = field(default_factory=list)
    book_id: int | None = None  # set -> a confirmed lead becomes an Order for this book
    in_stock: bool = True
    kind: str = "goods"  # goods: quantity + delivery address. service: number of people + date/city or time
    currency: str = "so'm"
    price_from: bool = False  # "from 150 $": the final price is set by a consultant
    documents: str | list | dict | None = None
    details_question: str | dict | None = None  # service: what to ask instead of the address
    ask_quantity: bool = True  # False (e.g. a consultation): quantity is 1, never asked
    prepayment: int = 0  # advance payment per person/unit, same currency; 0 -> none
    result_time: str | dict | None = None  # e.g. {"uz": "12 soatda", "tr": "12 saatte"}
    next_offers: dict = field(default_factory=dict)  # result -> product id offered next, e.g. {"ban": "deport"}
    booking: bool = False  # the details step offers consultation slots
    required_docs: list = field(default_factory=list)  # [{"id", "name": {lang: text}, "keywords": [...]}] checklist after the application

    @property
    def is_service(self) -> bool:
        return self.kind == "service"

    def title(self, lang: str) -> str:
        return _localized(self.name, lang)

    def desc(self, lang: str) -> str:
        return _localized(self.description, lang)

    def docs(self, lang: str) -> str:
        return _localized(self.documents, lang)

    def ask_details(self, lang: str) -> str:
        return _localized(self.details_question, lang)

    def all_names(self) -> str:
        if isinstance(self.name, dict):
            return " ".join(str(v) for v in self.name.values())
        return str(self.name)


def fmt_money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def money(value: int | None, currency: str, lang: str) -> str:
    v = fmt_money(value or 0)
    c = (currency or "so'm").strip()
    if c.lower() in SOM:
        return f"{v} " + {"uzc": "сўм", "ru": "сум"}.get(lang, "so'm")
    sym = {"usd": "$", "eur": "€", "try": "₺", "$": "$", "€": "€", "₺": "₺"}.get(c.lower())
    return f"{v} {sym}" if sym else f"{v} {c}"


_FROM = {"uz": "{p} dan", "uzc": "{p} дан", "tr": "{p} ve üzeri", "ru": "от {p}"}
_ON_REQUEST = {"uz": "narxi kelishiladi", "uzc": "нархи келишилади", "tr": "fiyat görüşülür", "ru": "цена договорная"}


def price_text(p: Product, lang: str, value: int | None = None) -> str:
    if p.price <= 0:
        return _ON_REQUEST.get(lang, _ON_REQUEST["uz"])
    text = money(p.price if value is None else value, p.currency, lang)
    return _FROM.get(lang, "{p}").format(p=text) if p.price_from else text


def _product(item: dict) -> Product:
    return Product(
        id=str(item["id"]), name=item["name"], price=int(item.get("price", 0)),
        description=item.get("description", ""), keywords=[str(k) for k in item.get("keywords", [])],
        book_id=item.get("book_id"), in_stock=bool(item.get("in_stock", True)),
        kind="service" if item.get("kind") == "service" else "goods", currency=str(item.get("currency", "so'm")),
        price_from=bool(item.get("price_from", False)), documents=item.get("documents"), details_question=item.get("details_question"),
        ask_quantity=bool(item.get("ask_quantity", True)), prepayment=int(item.get("prepayment", 0)),
        result_time=item.get("result_time"),
        next_offers=dict(item.get("next_offers") or {}),
        required_docs=list(item.get("required_docs") or []), booking=bool(item.get("booking", False)),
    )


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
    for item in raw.get("products", []) if isinstance(raw, dict) else raw:
        try:
            if not isinstance(item.get("name"), (str, dict)):
                raise TypeError
            out.append(_product(item))
        except (KeyError, TypeError, ValueError, AttributeError):
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
    words = re.findall(r"[a-z0-9']+", normalize(text).replace("\u0307", "").replace("ı", "i").replace("ş", "sh").replace("ç", "ch").replace("ğ", "g")
                       .replace("ö", "o").replace("ü", "u"))
    return {w.replace("'", "")[:5] for w in words if len(w) >= 3}


_STOP = {stem[:5] for stem in ("kitob", "kitap", "kniga", "narxi", "qancha", "kerak", "bormi", "salom", "assal", "alayk", "merha", "fiyat", "kadar",
                               "zdravs", "skolk", "stoit", "menga", "sizda", "bizga", "nechta", "tane", "adet", "uchun", "icin", "dlya", "xizmat",
                               "hizmet", "usluga", "istiyorum", "olmoqchi", "haqida", "hakkinda")}


def match(products: list[Product], message: str) -> Product | None:
    """Best product for a free-text message, or None. Prefix stems cope with Uzbek/Turkish suffixes."""
    words = _stems(message) - _STOP
    if not words:
        return None
    best, best_score = None, 0.0
    for p in products:
        keys = (_stems(p.all_names()) | {s for k in p.keywords for s in _stems(k)}) - _STOP
        if not keys:
            continue
        hit = len(words & keys)
        if hit == 0:
            continue
        score = hit + hit / len(keys)
        if score > best_score:
            best, best_score = p, score
    return best


def catalog_lines(products: list[Product], lang: str = "uz", limit: int = 10) -> str:
    return "\n".join(f"• {p.title(lang)} — {price_text(p, lang)}" for p in products[:limit])
