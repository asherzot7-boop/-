"""Passport data from the machine-readable zone (ICAO 9303, TD3: two lines of 44 characters).

An AI vision model only transcribes the two MRZ lines; every field is then
verified here with the MRZ check digits, so a misread digit is rejected
instead of being stored.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import date

log = logging.getLogger(__name__)

_W = (7, 3, 1)


def check_digit(s: str) -> int:
    total = 0
    for i, ch in enumerate(s):
        v = int(ch) if ch.isdigit() else (ord(ch) - 55 if ch.isalpha() else 0)   # A=10 ... Z=35, '<' = 0
        total += v * _W[i % 3]
    return total % 10


def _date(yymmdd: str, future: bool) -> date | None:
    try:
        yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
        year = 2000 + yy if (future or yy <= date.today().year % 100) else 1900 + yy
        return date(year, mm, dd)
    except ValueError:
        return None


def parse_mrz(text: str) -> dict | None:
    lines = [re.sub(r"\s+", "", x).upper() for x in text.splitlines()]
    lines = [x for x in lines if re.fullmatch(r"[A-Z0-9<]{44}", x)]
    if len(lines) < 2:
        return None
    l1, l2 = lines[-2], lines[-1]
    if not l1.startswith("P"):
        return None
    number, birth, expiry = l2[0:9], l2[13:19], l2[21:27]
    if (check_digit(number) != int(l2[9]) if l2[9].isdigit() else True) or \
       (check_digit(birth) != int(l2[19]) if l2[19].isdigit() else True) or \
       (check_digit(expiry) != int(l2[27]) if l2[27].isdigit() else True):
        return None
    names = l1[5:].split("<<", 1)
    surname = names[0].replace("<", " ").strip()
    given = names[1].replace("<", " ").strip() if len(names) > 1 else ""
    exp = _date(expiry, future=True)
    return {"country": l1[2:5].replace("<", ""), "surname": surname.title(), "given": given.title(),
            "number": number.replace("<", ""), "nationality": l2[10:13].replace("<", ""),
            "birth": (_date(birth, future=False) or "").__str__(), "expiry": str(exp) if exp else "", "sex": l2[20]}


PROMPT = ("This is a photo of a passport. Copy the two machine-readable zone (MRZ) lines at the bottom of the "
          "data page exactly, one per line, 44 characters each, using '<' for fillers. Output only those two lines. "
          "If no MRZ is visible, output NONE.")


async def read_mrz(data: bytes, mime: str, settings) -> str | None:
    """Ask the configured vision model for the MRZ text. Returns raw text or None."""
    b64 = base64.b64encode(data).decode()
    try:
        if settings.anthropic_api_key is not None and settings.anthropic_api_key.get_secret_value():
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value(), timeout=60, max_retries=1)
            resp = await client.messages.create(
                model=settings.sales_ai_model, max_tokens=1024, output_config={"effort": "low"},
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": PROMPT}]}])
            if resp.stop_reason == "refusal":
                return None
            return next((b.text for b in resp.content if b.type == "text"), None)
        if settings.llm_enabled:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=60, max_retries=1)
            resp = await client.chat.completions.create(model=settings.openai_model, messages=[{"role": "user", "content": [
                {"type": "text", "text": PROMPT}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}])
            return resp.choices[0].message.content
    except Exception as exc:
        log.warning("Паспортни ўқиб бўлмади: %s", type(exc).__name__)
    return None
