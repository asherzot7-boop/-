"""2. Янги вазифа базада сақланади (ва такрор буйруқ дубликат яратмайди)."""
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.database.models import Task, TaskStatus
from app.services.tasks import parse_due
from tests.conftest import DIRECTOR, STAFF


async def test_task_saved(env):
    reply = await env.service.dispatch(STAFF, "/task Таржимон билан 12-мактабда учрашув эртага", idempotency_key="tg:1:1")
    assert "Вазифа #1" in reply.text
    async with env.sf() as s:
        task = await s.get(Task, 1)
    assert task is not None
    assert "12-мактаб" in task.title
    assert task.created_by_id is not None
    assert task.due_at is not None  # «эртага» парсинг қилинди
    assert task.status == TaskStatus.NEW  # агент топилмади — ходимга


async def test_natural_language_is_a_task(env):
    await env.service.dispatch(DIRECTOR, "Харажатлар бўйича молия ҳисоботини тайёрла", idempotency_key="tg:1:2")
    async with env.sf() as s:
        parent = await s.scalar(select(Task).where(Task.parent_id.is_(None)))
        subs = (await s.scalars(select(Task).where(Task.parent_id == parent.id))).all()
    assert parent.status == TaskStatus.DONE
    assert [t.assigned_agent for t in subs] == ["finance"]


async def test_duplicate_message_does_not_duplicate_task(env):
    r1 = await env.service.dispatch(STAFF, "/task Янги китоб лойиҳасини оч", idempotency_key="tg:5:77")
    r2 = await env.service.dispatch(STAFF, "/task Янги китоб лойиҳасини оч", idempotency_key="tg:5:77")
    assert "аллақачон" in r2.text and "аллақачон" not in r1.text
    async with env.sf() as s:
        assert await s.scalar(select(func.count()).select_from(Task).where(Task.parent_id.is_(None))) == 1
        from app.database.models import Book

        assert await s.scalar(select(func.count()).select_from(Book)) == 1


def test_parse_due():
    tz = ZoneInfo("Asia/Tashkent")
    d = parse_due("муддат 2026-11-05", tz)
    assert (d.year, d.month, d.day, d.hour) == (2026, 11, 5, 13)  # 18:00 Тошкент = 13:00 UTC
    assert parse_due("25.12.2026 гача", tz).month == 12
    assert parse_due("муддатсиз", tz) is None
