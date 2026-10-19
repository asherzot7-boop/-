"""Key/value system settings stored in the settings table."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Setting

AUTOMATION_ENABLED = "automation_enabled"
LAST_DAILY_REPORT = "last_daily_report_date"
SALES_SNAPSHOT = "sales_report_snapshot"
FINANCE_SNAPSHOT = "finance_report_snapshot"

DEFAULTS: dict[str, Any] = {AUTOMATION_ENABLED: True}


async def get_setting(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = await session.scalar(select(Setting).where(Setting.key == key))
    if row is None:
        return DEFAULTS.get(key, default)
    return json.loads(row.value)


async def set_setting(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.scalar(select(Setting).where(Setting.key == key))
    raw = json.dumps(value, ensure_ascii=False, default=str)
    if row is None:
        session.add(Setting(key=key, value=raw))
    else:
        row.value = raw
    await session.flush()


async def automation_enabled(session: AsyncSession) -> bool:
    return bool(await get_setting(session, AUTOMATION_ENABLED, True))
