"""Drop updates that Telegram delivers twice (retries, double taps).

This is the first line of defence. The second is the idempotency_key on
tasks/orders/expenses/approvals, which survives a restart.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class DedupMiddleware(BaseMiddleware):
    def __init__(self, max_size: int = 5000) -> None:
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.max_size = max_size

    @staticmethod
    def key(event: TelegramObject) -> str | None:
        if isinstance(event, Message):
            return f"m:{event.chat.id}:{event.message_id}"
        if isinstance(event, CallbackQuery):
            return f"c:{event.id}"
        return None

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        k = self.key(event)
        if k is not None:
            if k in self.seen:
                return None
            self.seen[k] = None
            if len(self.seen) > self.max_size:
                self.seen.popitem(last=False)
        return await handler(event, data)
