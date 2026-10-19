"""1. Рухсатсиз Telegram фойдаланувчиси рад этилади."""
from datetime import datetime

from aiogram.types import Chat, Message
from aiogram.types import User as TgUser
from sqlalchemy import select

from app.bot.middlewares.auth import AuthMiddleware
from app.database.models import AuditLog, Task
from tests.conftest import DIRECTOR, STAFF, STRANGER, VIEWER


def tg_message(user_id: int, text: str = "/start") -> Message:
    return Message(
        message_id=1, date=datetime.now(), text=text,
        chat=Chat(id=user_id, type="private"), from_user=TgUser(id=user_id, is_bot=False, first_name="Test"),
    )


async def test_middleware_blocks_unknown_user(env):
    called = []

    async def handler(event, data):
        called.append(data["app_user"])

    mw = AuthMiddleware(env.sf)
    await mw(handler, tg_message(STRANGER), {})
    assert called == []

    async with env.sf() as s:
        log = await s.scalar(select(AuditLog).where(AuditLog.action == "access.denied"))
    assert log is not None and log.telegram_id == STRANGER


async def test_middleware_passes_known_user(env):
    called = []

    async def handler(event, data):
        called.append(data["app_user"].telegram_id)

    await AuthMiddleware(env.sf)(handler, tg_message(DIRECTOR), {})
    assert called == [DIRECTOR]


async def test_service_rejects_stranger_and_creates_nothing(env):
    reply = await env.service.dispatch(STRANGER, "/task Янги лойиҳа оч", idempotency_key="k1")
    assert "Рухсат йўқ" in reply.text
    async with env.sf() as s:
        assert (await s.scalars(select(Task))).all() == []


async def test_roles(env):
    assert "Рухсат йўқ" not in (await env.service.dispatch(VIEWER, "/books")).text
    assert "ходим" in (await env.service.dispatch(VIEWER, "/task нимадир")).text  # viewer cannot write
    assert "ходим" in (await env.service.dispatch(VIEWER, "/books new «Тест»")).text
    assert "раҳбар" in (await env.service.dispatch(STAFF, "/approve")).text  # staff cannot approve
    assert "раҳбар" in (await env.service.dispatch(STAFF, "/pause")).text
    assert "Тасдиқ" in (await env.service.dispatch(DIRECTOR, "/approve")).text


async def test_deactivated_user_is_rejected(tmp_path):
    from tests.conftest import build_env, make_settings

    e = await build_env(make_settings(tmp_path, staff_ids=[STAFF]))
    from app.security.permissions import sync_users

    async with e.sf() as s:
        await sync_users(s, [DIRECTOR], [], [])  # STAFF removed from .env
    assert "Рухсат йўқ" in (await e.service.dispatch(STAFF, "/start")).text
    await e.engine.dispose()
