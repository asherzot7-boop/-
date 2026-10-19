"""Text helpers shared by agents (Uzbek Cyrillic and Latin)."""
from __future__ import annotations

import re

QUOTED = re.compile(r"[«\"“]([^»\"”]{2,})[»\"”]")
ID_REF = re.compile(r"#\s?(\d+)")
PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s?%")
AMOUNT = re.compile(r"(\d[\d\s]{0,15}\d|\d)\s*(?:сўм|so'm|som|сум|uzs)?", re.I)
TEXT_MARKERS = ("матн:", "matn:", "текст:", "text:")


def extract_body_text(text: str) -> str | None:
    """Long text the user attached: after «матн:» marker, or a long quoted block."""
    low = text.lower()
    for marker in TEXT_MARKERS:
        idx = low.find(marker)
        if idx >= 0:
            body = text[idx + len(marker):].strip()
            return body or None
    for m in QUOTED.finditer(text):
        if len(m.group(1)) > 80:
            return m.group(1).strip()
    return None


def extract_title(text: str) -> str | None:
    for m in QUOTED.finditer(text):
        if len(m.group(1)) <= 80:
            return m.group(1).strip()
    return None


def extract_id(text: str) -> int | None:
    m = ID_REF.search(text)
    return int(m.group(1)) if m else None


def extract_percent(text: str) -> float | None:
    m = PERCENT.search(text)
    return float(m.group(1).replace(",", ".")) if m else None


def extract_amount(text: str) -> int | None:
    """Largest number in the text, spaces allowed as thousands separators (1 500 000)."""
    best = None
    for m in AMOUNT.finditer(ID_REF.sub(" ", PERCENT.sub(" ", text))):
        try:
            value = int(m.group(1).replace(" ", ""))
        except ValueError:
            continue
        if best is None or value > best:
            best = value
    return best


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def words(text: str) -> list[str]:
    return re.findall(r"[\wʻʼ'’-]+", text)
