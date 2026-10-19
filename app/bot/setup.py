"""Build the aiogram Bot and Dispatcher."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, ErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.commands import CommandService
from app.bot.handlers.common import build_router
from app.bot.middlewares.auth import AuthMiddleware
from app.bot.middlewares.dedup import DedupMiddleware

log = logging.getLogger(__name__)

BOT_COMMANDS = [
    ("start", "Бошлаш"), ("help", "Йўриқнома"), ("task", "Вазифа яратиш"), ("today", "Бугунги ишлар"),
    ("books", "Китоблар реестри"), ("orders", "Буюртмалар"), ("clients", "Мижозлар"), ("finance", "Молиявий ҳисобот"),
    ("report", "Умумий ҳисобот"), ("approve", "Тасдиқ кутаётганлар"), ("agents", "Агентлар ҳолати"),
    ("pause", "Автоматикани тўхтатиш"), ("resume", "Автоматикани ёқиш"),
]


def build_dispatcher(service: CommandService, session_factory: async_sessionmaker[AsyncSession]) -> Dispatcher:
    dp = Dispatcher()
    dedup = DedupMiddleware()
    auth = AuthMiddleware(session_factory)
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(dedup)
        observer.outer_middleware(auth)
    dp.include_router(build_router(service))

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.error("Ботда хатолик: %s: %s", type(event.exception).__name__, event.exception)
        msg = getattr(event.update, "message", None)
        if msg is not None:
            try:
                await msg.answer("❗ Хатолик юз берди. Бот ишлашда давом этмоқда.")
            except Exception:
                pass
        return True

    return dp


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in BOT_COMMANDS])
