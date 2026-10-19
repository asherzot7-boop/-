"""Audit trail for important actions."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog, User


async def log_action(
    session: AsyncSession,
    action: str,
    user: User | None = None,
    entity: str | None = None,
    entity_id: int | None = None,
    details: dict | None = None,
    telegram_id: int | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        user_id=user.id if user else None,
        telegram_id=user.telegram_id if user else telegram_id,
        entity=entity,
        entity_id=entity_id,
        details=details or {},
    )
    session.add(entry)
    await session.flush()
    return entry
