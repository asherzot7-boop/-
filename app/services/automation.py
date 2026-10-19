"""Background automation: deadline reminders, overdue alerts, daily report.

Controlled by the automation_enabled setting (/pause, /resume).
Each notification is sent once: flags on the task / a date in settings
prevent repeats, also after a restart.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.database.models import User, utcnow
from app.notify import Notifier, director_ids
from app.services import reporting, settings_store, tasks

log = logging.getLogger(__name__)


def _local(dt: datetime, tz: ZoneInfo) -> str:
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%d.%m.%Y %H:%M")


async def run_once(session: AsyncSession, notifier: Notifier, settings: Settings, now: datetime | None = None) -> int:
    """One automation pass. Returns the number of messages sent."""
    if not await settings_store.automation_enabled(session):
        return 0
    now = now or utcnow()
    tz = settings.tz
    directors = await director_ids(session)
    sent = 0

    for t in await tasks.due_between(session, now, now + timedelta(hours=settings.reminder_hours_before)):
        if t.reminder_sent or t.parent_id is not None:
            continue
        creator = await session.get(User, t.created_by_id) if t.created_by_id else None
        targets = {creator.telegram_id} if creator and creator.is_active else set(directors)
        for tid in targets:
            await notifier.send(tid, f"⏰ Эслатма: #{t.id} «{t.title[:100]}» муддати {_local(t.due_at, tz)}")
            sent += 1
        t.reminder_sent = True

    overdue = [t for t in await tasks.overdue_tasks(session, now) if not t.overdue_notified and t.parent_id is None]
    if overdue:
        text = "🔴 Кечиккан вазифалар:\n" + "\n".join(f"#{t.id} {t.title[:80]} — муддат {_local(t.due_at, tz)}" for t in overdue[:20])
        for tid in directors:
            await notifier.send(tid, text)
            sent += 1
        for t in overdue:
            t.overdue_notified = True

    local_now = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    today = local_now.date().isoformat()
    if local_now.hour >= settings.daily_report_hour and await settings_store.get_setting(session, settings_store.LAST_DAILY_REPORT) != today:
        kpi = await reporting.build_kpi(session, "day", tz, now)
        text = reporting.format_kpi(kpi, tz)
        for tid in directors:
            await notifier.send(tid, text)
            sent += 1
        await settings_store.set_setting(session, settings_store.LAST_DAILY_REPORT, today)

    await session.commit()
    return sent


async def scheduler_loop(session_factory: async_sessionmaker[AsyncSession], notifier: Notifier, settings: Settings) -> None:
    log.info("Автоматика ишга тушди (ҳар %s сония)", settings.scheduler_interval_seconds)
    while True:
        try:
            async with session_factory() as session:
                await run_once(session, notifier, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Автоматика қадамида хатолик")
        await asyncio.sleep(settings.scheduler_interval_seconds)
