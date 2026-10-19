"""Telegram ID access check. Unknown users never reach a handler."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.security import audit
from app.security.permissions import get_active_user

log = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.sf = session_factory

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is None or from_user.is_bot:
            return None
        async with self.sf() as session:
            user = await get_active_user(session, from_user.id)
            if user is None:
                await audit.log_action(session, "access.denied", telegram_id=from_user.id, details={"event": type(event).__name__})
                await session.commit()
        if user is None:
            log.info("Рухсатсиз фойдаланувчи рад этилди: %s", from_user.id)
            await self._deny(event, from_user.id)
            return None
        data["app_user"] = user
        return await handler(event, data)

    @staticmethod
    async def _deny(event: TelegramObject, telegram_id: int) -> None:
        text = f"⛔ Рухсат йўқ. Сизнинг Telegram ID: {telegram_id}. Раҳбардан рўйхатга қўшишни сўранг."
        try:
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Рухсат йўқ", show_alert=True)
        except Exception:  # no bot bound (tests) or network error
            log.debug("Рад этиш хабарини юбориб бўлмади")
