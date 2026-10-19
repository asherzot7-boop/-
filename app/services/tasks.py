"""Tasks: create, change status, query by deadline."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Task, TaskStatus, User, utcnow

OPEN_STATUSES = (TaskStatus.NEW, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.WAITING_APPROVAL)

STATUS_NAMES = {
    TaskStatus.NEW: "янги",
    TaskStatus.ASSIGNED: "топширилди",
    TaskStatus.IN_PROGRESS: "бажарилмоқда",
    TaskStatus.WAITING_APPROVAL: "тасдиқ кутмоқда",
    TaskStatus.DONE: "бажарилди",
    TaskStatus.FAILED: "хато",
    TaskStatus.CANCELLED: "бекор қилинди",
}

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def parse_due(text: str, tz: ZoneInfo, now: datetime | None = None) -> datetime | None:
    """Find a deadline in free text. Returns naive UTC end-of-day, or None.

    Understands 2026-10-25, 25.10.2026, «бугун», «эртага», «индинга».
    """
    local_now = (now or datetime.now(tz)).astimezone(tz)
    day = None
    if m := _ISO.search(text):
        day = datetime(int(m[1]), int(m[2]), int(m[3])).date()
    elif m := _DMY.search(text):
        day = datetime(int(m[3]), int(m[2]), int(m[1])).date()
    else:
        low = text.lower()
        for word, delta in (("индинга", 2), ("ertaga", 1), ("эртага", 1), ("bugun", 0), ("бугун", 0)):
            if word in low:
                day = (local_now + timedelta(days=delta)).date()
                break
    if day is None:
        return None
    local_end = datetime.combine(day, time(18, 0), tzinfo=tz)
    return local_end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


async def create_task(
    session: AsyncSession,
    title: str,
    created_by: User | None = None,
    description: str | None = None,
    due_at: datetime | None = None,
    idempotency_key: str | None = None,
    book_id: int | None = None,
    parent_id: int | None = None,
    assigned_agent: str | None = None,
    step_order: int = 0,
) -> tuple[Task, bool]:
    """Create a task. Returns (task, created). A repeated idempotency key returns the existing task."""
    if idempotency_key:
        existing = await session.scalar(select(Task).where(Task.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
    task = Task(
        title=title[:500],
        description=description,
        due_at=due_at,
        created_by_id=created_by.id if created_by else None,
        idempotency_key=idempotency_key,
        book_id=book_id,
        parent_id=parent_id,
        assigned_agent=assigned_agent,
        status=TaskStatus.ASSIGNED if assigned_agent else TaskStatus.NEW,
        step_order=step_order,
    )
    session.add(task)
    await session.flush()
    return task, True


async def set_status(session: AsyncSession, task: Task, status: TaskStatus, result: str | None = None) -> Task:
    task.status = status
    if result is not None:
        task.result = result
    await session.flush()
    return task


async def get_task(session: AsyncSession, task_id: int) -> Task | None:
    return await session.get(Task, task_id)


async def open_tasks(session: AsyncSession) -> list[Task]:
    rows = await session.scalars(
        select(Task).where(Task.status.in_(OPEN_STATUSES), Task.parent_id.is_(None)).order_by(Task.due_at.is_(None), Task.due_at, Task.id)
    )
    return list(rows)


async def overdue_tasks(session: AsyncSession, now: datetime | None = None) -> list[Task]:
    now = now or utcnow()
    rows = await session.scalars(
        select(Task).where(Task.status.in_(OPEN_STATUSES), Task.due_at.is_not(None), Task.due_at < now).order_by(Task.due_at)
    )
    return list(rows)


async def due_between(session: AsyncSession, start: datetime, end: datetime) -> list[Task]:
    rows = await session.scalars(
        select(Task).where(Task.status.in_(OPEN_STATUSES), Task.due_at >= start, Task.due_at < end).order_by(Task.due_at)
    )
    return list(rows)


async def failed_tasks(session: AsyncSession, since: datetime | None = None) -> list[Task]:
    q = select(Task).where(Task.status == TaskStatus.FAILED)
    if since:
        q = q.where(Task.updated_at >= since)
    return list(await session.scalars(q.order_by(Task.id.desc())))
