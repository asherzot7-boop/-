"""Automation (reminders, overdue, daily report, pause) and the REST API."""
from datetime import timedelta

from httpx import ASGITransport, AsyncClient

from app.api.routes import create_api
from app.database.models import Task, utcnow
from app.notify import ConsoleNotifier
from app.services import automation, tasks
from tests.conftest import DIRECTOR, STAFF, build_env, make_settings


async def test_reminder_overdue_and_daily_report_sent_once(env):
    async with env.sf() as s:
        from app.security.permissions import get_active_user

        staff = await get_active_user(s, STAFF)
        await tasks.create_task(s, "Макетни топшириш", created_by=staff, due_at=utcnow() + timedelta(hours=3))
        await tasks.create_task(s, "Кечиккан иш", due_at=utcnow() - timedelta(hours=3))
        await s.commit()

    n = ConsoleNotifier()
    async with env.sf() as s:
        await automation.run_once(s, n, env.settings)
    targets = [t for t, _ in n.sent]
    texts = "\n".join(m for _, m in n.sent)
    assert STAFF in targets and "Эслатма" in texts
    assert DIRECTOR in targets and "Кечиккан" in texts

    n2 = ConsoleNotifier()
    async with env.sf() as s:
        await automation.run_once(s, n2, env.settings)
    assert not [m for _, m in n2.sent if "Эслатма" in m or "Кечиккан" in m]  # not repeated


async def test_daily_report(env):
    n = ConsoleNotifier()
    async with env.sf() as s:
        now = utcnow().replace(hour=16, minute=0, second=0, microsecond=0)  # 21:00 Тошкент
        await automation.run_once(s, n, env.settings, now=now)
        await automation.run_once(s, n, env.settings, now=now + timedelta(minutes=5))
    reports = [m for _, m in n.sent if "Кунлик ҳисобот" in m]
    assert len(reports) == 1


async def test_pause_stops_automation(env):
    await env.service.dispatch(DIRECTOR, "/pause")
    async with env.sf() as s:
        await tasks.create_task(s, "Кечиккан", due_at=utcnow() - timedelta(hours=1))
        await s.commit()
        n = ConsoleNotifier()
        assert await automation.run_once(s, n, env.settings) == 0
    await env.service.dispatch(DIRECTOR, "/resume")
    async with env.sf() as s:
        assert await automation.run_once(s, n, env.settings) >= 1
        assert (await s.get(Task, 1)).overdue_notified


async def test_api_health_and_token(tmp_path):
    e = await build_env(make_settings(tmp_path, api_token="test-api-token-123"))
    app = create_api(e.settings, e.sf)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        h = await c.get("/health")
        assert h.status_code == 200 and h.json()["database"] == "ok" and h.json()["demo_mode"] is True
        assert (await c.get("/api/kpi")).status_code == 401
        r = await c.get("/api/kpi?period=week", headers={"X-API-Token": "test-api-token-123"})
        assert r.status_code == 200 and r.json()["books_total"] == 0
    await e.engine.dispose()


async def test_api_disabled_without_token(env):
    app = create_api(env.settings, env.sf)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/books", headers={"X-API-Token": "x"})).status_code == 403
