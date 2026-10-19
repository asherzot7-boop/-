"""aiogram wiring end to end, with a fake Telegram HTTP session (no network)."""
from datetime import datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from app.bot.setup import build_dispatcher
from tests.conftest import DIRECTOR, STRANGER


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list = []

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=len(self.sent), date=datetime.now(), chat=Chat(id=method.chat_id, type="private"), text=method.text)
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    async def close(self) -> None:
        pass


def msg_update(update_id: int, user_id: int, text: str, message_id: int | None = None) -> Update:
    return Update(update_id=update_id, message=Message(
        message_id=message_id or update_id, date=datetime.now(), text=text,
        chat=Chat(id=user_id, type="private"), from_user=TgUser(id=user_id, is_bot=False, first_name="T"),
    ))


def texts(session: FakeSession) -> list[str]:
    return [m.text for m in session.sent if isinstance(m, SendMessage)]


async def test_bot_flow(env):
    session = FakeSession()
    bot = Bot("123456:TEST-token-not-real", session=session)
    dp = build_dispatcher(env.service, env.sf)

    await dp.feed_update(bot, msg_update(1, STRANGER, "/start"))
    assert "Рухсат йўқ" in texts(session)[-1]

    await dp.feed_update(bot, msg_update(2, DIRECTOR, "/start"))
    assert "хуш келибсиз" in texts(session)[-1]

    # same Telegram message delivered twice -> answered once, task created once
    await dp.feed_update(bot, msg_update(3, DIRECTOR, "/books new «Тест китоб»"))
    await dp.feed_update(bot, msg_update(4, DIRECTOR, "#1 китобни босмахонага юбор", message_id=40))
    n = len(texts(session))
    await dp.feed_update(bot, msg_update(4, DIRECTOR, "#1 китобни босмахонага юбор", message_id=40))
    assert len(texts(session)) == n

    await dp.feed_update(bot, msg_update(5, DIRECTOR, "/approve"))
    last = [m for m in session.sent if isinstance(m, SendMessage)][-1]
    assert last.reply_markup is not None
    data = last.reply_markup.inline_keyboard[0][0].callback_data
    assert data == "ap:1:1"

    cb = Update(update_id=6, callback_query=CallbackQuery(
        id="cb1", chat_instance="x", data=data, from_user=TgUser(id=DIRECTOR, is_bot=False, first_name="T"),
        message=Message(message_id=99, date=datetime.now(), chat=Chat(id=DIRECTOR, type="private"), text="..."),
    ))
    await dp.feed_update(bot, cb)
    assert any(isinstance(m, AnswerCallbackQuery) for m in session.sent)
    assert "Тасдиқланди" in texts(session)[-1]
    await dp.feed_update(bot, msg_update(7, DIRECTOR, "/books"))
    assert "босмахона" in texts(session)[-1]
