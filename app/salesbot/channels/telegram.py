"""Customer-facing Telegram bot (SALES_BOT_TOKEN).

Anyone may write to it: there is no staff authorisation here. It also
answers Telegram Business chats, so the shop's own account can reply
automatically once the bot is connected in Telegram Business settings.
"""
from __future__ import annotations

import logging

from aiogram import Dispatcher, F, Router
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.salesbot import lang as L
from app.salesbot.agent import SalesAgent

log = logging.getLogger(__name__)


def _keyboard(reply, business: bool):
    if business:
        return None  # reply keyboards are not available in business chats
    if reply.ask_phone:
        return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=L.text(reply.language, "phone_button"), request_contact=True)]],
                                   resize_keyboard=True, one_time_keyboard=True)
    return ReplyKeyboardRemove()


def build_sales_router(agent: SalesAgent) -> Router:
    router = Router(name="sales")

    async def _handle(message: Message, business: bool) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        if business and message.business_connection_id and message.chat.id != message.from_user.id:
            return  # our own outgoing message in a business chat
        text = message.text or message.caption or ""
        phone = None
        if message.contact is not None:
            phone = message.contact.phone_number
            text = text or phone
        if text.startswith("/start"):
            text = ""
        channel = "telegram_business" if business else "telegram"
        reply = await agent.handle(channel, str(message.chat.id), text, display_name=message.from_user.full_name, phone=phone,
                                   message_id=f"{message.chat.id}:{message.message_id}")
        if not reply.text:
            return
        if business:
            await message.bot.send_message(message.chat.id, reply.text, business_connection_id=message.business_connection_id)
        else:
            await message.answer(reply.text, reply_markup=_keyboard(reply, business))

    @router.message(F.text | F.contact)
    async def on_message(message: Message) -> None:
        await _handle(message, business=False)

    @router.business_message(F.text)
    async def on_business(message: Message) -> None:
        await _handle(message, business=True)

    return router


def build_sales_dispatcher(agent: SalesAgent) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(build_sales_router(agent))

    @dp.errors()
    async def on_error(event) -> bool:
        log.error("Сотув ботида хатолик: %s", type(event.exception).__name__)
        return True

    return dp
