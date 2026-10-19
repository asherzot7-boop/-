"""Tiny in-process event bus.

Services emit events after they change data (order_changed, expense_added,
...). Subscribers run in the same session, so the derived data they write is
committed together with the change that triggered it.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, dict], Awaitable[None]]
_handlers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event: str, handler: Handler) -> None:
    if handler not in _handlers[event]:
        _handlers[event].append(handler)


async def emit(session: AsyncSession, event: str, payload: dict | None = None) -> None:
    for handler in list(_handlers.get(event, [])):
        try:
            await handler(session, payload or {})
        except Exception:
            # A failing subscriber must not break the operation that emitted the event.
            log.exception("Event handler failed for %s", event)
