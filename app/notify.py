"""Outgoing notifications (reminders, overdue alerts, daily report)."""
from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.commands import chunks
from app.database.models import Role, User

log = logging.getLogger(__name__)


class Notifier(Protocol):
    async def send(self, telegram_id: int, text: str) -> None: ...


class TelegramNotifier:
    def __init__(self, bot) -> None:
        self.bot = bot

    async def send(self, telegram_id: int, text: str) -> None:
        for part in chunks(text):
            try:
                await self.bot.send_message(telegram_id, part)
            except Exception as exc:
                log.warning("Хабар юборилмади (%s): %s", telegram_id, type(exc).__name__)


class ConsoleNotifier:
    """Demo mode: print notifications instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, telegram_id: int, text: str) -> None:
        self.sent.append((telegram_id, text))
        log.info("[demo хабар → %s]\n%s", telegram_id, text)


async def director_ids(session: AsyncSession) -> list[int]:
    rows = await session.scalars(select(User.telegram_id).where(User.role == Role.DIRECTOR, User.is_active.is_(True)))
    return list(rows)
