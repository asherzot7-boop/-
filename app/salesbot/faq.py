"""Knowledge base: answers the consultants approved.

Two sources:
- sales_faq.json (SALES_FAQ_PATH): curated entries, questions and answers per language;
- faq_entries table: answers a manager gave with /answer, saved with the client's question.

The agent answers only from here. A question it cannot match goes to a
manager, and the manager's answer becomes a new entry.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.salesbot.catalog import _localized, _stems
from app.salesbot.lang import normalize
from app.salesbot.models import FaqEntry

log = logging.getLogger(__name__)

_QUESTION = re.compile(
    r"\?|qanday|qachon|nega|nimaga|qayer|qaerda|qancha vaqt|necha kun|necha soat|bo'ladimi|mumkinmi|kafolat|qanaqa|"
    r"ne zaman|nasıl|nerede|neden|kaç gün|mümkün mü|garanti|"
    r"как |когда|где |почему|сколько времени|сколько дней|можно ли|гарант|"
    r"қандай|қачон|нега|қаерда|неча кун|бўладими|мумкинми",
    re.IGNORECASE,
)
_FAQ_STOP = {s.replace("'", "")[:5] for s in ("qanday", "qachon", "qancha", "kerak", "bo'ladi", "bormi", "sizlar", "sizda", "bizga", "menga", "nasil", "zaman",
                              "kadar", "nerede", "kogda", "skolko", "mozhno", "nuzhno", "uchun", "icin", "dlya", "salom", "assalomu", "merhaba",
                              "zdravstvuyte", "iltimos", "lutfen", "pozhaluysta", "rahmat")}


@dataclass
class Faq:
    id: str
    questions: list[str]
    answer: str | dict
    db_id: int | None = None
    language: str | None = None
    stems: set[str] = field(default_factory=set)

    def text(self, lang: str) -> str:
        if isinstance(self.answer, str):
            return self.answer
        return _localized(self.answer, lang)


def is_question(text: str) -> bool:
    return bool(_QUESTION.search(text) or _QUESTION.search(normalize(text)))


def _key_stems(text: str) -> set[str]:
    return _stems(text) - _FAQ_STOP


def load_file(path: str | None) -> list[Faq]:
    if not path or not Path(path).exists():
        return []
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.error("FAQ файли ўқилмади (%s): %s", path, exc)
        return []
    out = []
    for item in raw.get("faq", []):
        try:
            qs = item["questions"]
            questions = [q for v in qs.values() for q in (v if isinstance(v, list) else [v])] if isinstance(qs, dict) else list(qs)
            out.append(Faq(id=str(item["id"]), questions=questions, answer=item["answer"]))
        except (KeyError, TypeError, AttributeError):
            log.warning("FAQ'даги нотўғри ёзув ўтказиб юборилди: %s", item)
    return out


async def load_all(session: AsyncSession, path: str | None) -> list[Faq]:
    items = load_file(path)
    for row in await session.scalars(select(FaqEntry).where(FaqEntry.active.is_(True))):
        items.append(Faq(id=f"db-{row.id}", questions=[row.question], answer=row.answer, db_id=row.id, language=row.language))
    for f in items:
        f.stems = set().union(*(_key_stems(q) for q in f.questions)) if f.questions else set()
    return items


def match(items: list[Faq], message: str, min_hits: int = 2, min_share: float = 0.5) -> Faq | None:
    """Best entry when enough of one of its question's words are in the message."""
    words = _key_stems(message)
    if not words:
        return None
    best, best_score = None, 0.0
    for f in items:
        for q in f.questions:
            keys = _key_stems(q)
            if not keys:
                continue
            hit = len(words & keys)
            share = hit / len(keys)
            if hit >= min(min_hits, len(keys)) and share >= min_share:
                score = hit + share
                if score > best_score:
                    best, best_score = f, score
    return best
