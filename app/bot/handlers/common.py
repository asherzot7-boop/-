"""aiogram handlers: thin wrappers around CommandService."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.bot.commands import CommandService, chunks
from app.bot.keyboards.inline import build_markup

log = logging.getLogger(__name__)


def build_router(service: CommandService) -> Router:
    router = Router(name="main")

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        key = f"tg:{message.chat.id}:{message.message_id}"
        name = message.from_user.full_name if message.from_user else None
        reply = await service.dispatch(message.from_user.id, message.text, idempotency_key=key, full_name=name)
        parts = chunks(reply.text)
        for i, part in enumerate(parts):
            markup = build_markup(reply.buttons) if i == len(parts) - 1 else None
            await message.answer(part, reply_markup=markup)

    @router.message()
    async def on_other(message: Message) -> None:
        await message.answer("Ҳозирча фақат матнли хабарлар қабул қилинади. /help")

    @router.callback_query(F.data.startswith("ap:"))
    async def on_approval(callback: CallbackQuery) -> None:
        try:
            _, approval_id, flag = callback.data.split(":")
            reply = await service.decide(callback.from_user.id, int(approval_id), flag == "1")
        except ValueError:
            await callback.answer("Нотўғри тугма", show_alert=True)
            return
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer(reply.text)

    return router
