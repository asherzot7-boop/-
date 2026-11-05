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


def _attachment(message: Message):
    """(kind, file_id, file_name, mime, size) or None."""
    if message.document is not None:
        d = message.document
        return "document", d.file_id, d.file_name, d.mime_type, d.file_size
    if message.photo:
        p = message.photo[-1]  # the largest size
        return "photo", p.file_id, f"photo_{message.message_id}.jpg", "image/jpeg", p.file_size
    if message.voice is not None:
        v = message.voice
        return "audio", v.file_id, f"voice_{message.message_id}.ogg", v.mime_type or "audio/ogg", v.file_size
    if message.audio is not None:
        a = message.audio
        return "audio", a.file_id, a.file_name, a.mime_type, a.file_size
    return None


async def _download(bot, file_id: str) -> bytes | None:
    import io

    try:
        buf = io.BytesIO()
        await bot.download(file_id, destination=buf)
        return buf.getvalue()
    except Exception as exc:
        log.warning("Telegram файли юклаб олинмади: %s", type(exc).__name__)
        return None


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
        source = None
        if text.startswith("/start"):
            payload = text[6:].strip()
            if payload:
                source = {"type": "link", "id": payload[:64]}
            text = ""
        channel = "telegram_business" if business else "telegram"
        extra = {"business_connection_id": message.business_connection_id} if business and message.business_connection_id else None
        msg_id = f"{message.chat.id}:{message.message_id}"
        attachment = _attachment(message)
        if attachment is not None:
            kind, file_id, name, mime, size = attachment
            too_big = size is not None and size > agent.settings.sales_max_file_mb * 1024 * 1024
            data = None if too_big else await _download(message.bot, file_id)
            reply = await agent.handle_file(channel, str(message.chat.id), kind=kind, data=data, too_big=too_big, file_name=name, mime=mime,
                                            caption=message.caption, message_id=msg_id, display_name=message.from_user.full_name, extra=extra)
        else:
            reply = await agent.handle(channel, str(message.chat.id), text, display_name=message.from_user.full_name, phone=phone,
                                       message_id=msg_id, extra=extra, source=source)
        if not reply.text:
            return
        if business:
            await message.bot.send_message(message.chat.id, reply.text, business_connection_id=message.business_connection_id)
        else:
            await message.answer(reply.text, reply_markup=_keyboard(reply, business))
        if reply.voice:                                     # a voice note answered with a voice note
            from aiogram.types import BufferedInputFile

            kw = {"business_connection_id": message.business_connection_id} if business else {}
            await message.bot.send_voice(message.chat.id, BufferedInputFile(reply.voice, filename="reply.ogg"), **kw)

    @router.message(F.text | F.contact | F.document | F.photo | F.voice | F.audio)
    async def on_message(message: Message) -> None:
        await _handle(message, business=False)

    @router.business_message(F.text | F.document | F.photo | F.voice | F.audio)
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
