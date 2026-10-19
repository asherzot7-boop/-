"""Logging with secret redaction.

Every record passes through RedactingFilter, which masks the configured
secrets (bot token, OpenAI key, API token) and anything that looks like a
Telegram bot token or an OpenAI key, even if it reaches the log via an
exception message from a third-party library.
"""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot token
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),  # OpenAI style key
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._-]{16,}"),
]
MASK = "***"


def redact(text: str, secrets: list[str] | None = None) -> str:
    for secret in secrets or []:
        if secret:
            text = text.replace(secret, MASK)
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + MASK, text)
    return text


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self.secrets = [s for s in (secrets or []) if s]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            return True
        if record.exc_info:
            formatter = logging.Formatter()
            message = message + "\n" + formatter.formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = None
        record.msg = redact(message, self.secrets)
        record.args = None
        return True


def setup_logging(level: str = "INFO", secrets: list[str] | None = None) -> RedactingFilter:
    root = logging.getLogger()
    root.setLevel(level.upper())
    flt = RedactingFilter(secrets)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(flt)
    # httpx logs full request URLs, which for Telegram contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    return flt
