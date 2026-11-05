"""Messages the business starts: manager replies, payment confirmations, reminders.

Each channel has its own sender. A channel without one (a phone call) is
reported back as not delivered, so the manager knows to call instead.
"""
from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)


class ChannelSender(Protocol):
    async def send(self, channel: str, recipient: str, text: str, extra: dict | None = None) -> bool: ...


class TelegramSender:
    def __init__(self, bot) -> None:
        self.bot = bot

    async def send(self, channel: str, recipient: str, text: str, extra: dict | None = None) -> bool:
        kwargs = {}
        if channel == "telegram_business":
            bc = (extra or {}).get("business_connection_id")
            if not bc:
                return False
            kwargs["business_connection_id"] = bc
        try:
            await self.bot.send_message(int(recipient), text, **kwargs)
            return True
        except Exception as exc:
            log.warning("Telegram'га юборилмади (%s): %s", recipient, type(exc).__name__)
            return False


class MetaChannelSender:
    def __init__(self, meta_sender) -> None:
        self.meta = meta_sender

    async def send(self, channel: str, recipient: str, text: str, extra: dict | None = None) -> bool:
        return await self.meta.send(channel, recipient, text)

    async def send_template(self, channel: str, recipient: str, name: str, lang: str) -> bool:
        return await self.meta.send_template(recipient, name, lang)


class WebSender:
    """The site widget polls for new messages; anything logged for the lead is delivered, so sending is a no-op."""

    async def send(self, channel: str, recipient: str, text: str, extra: dict | None = None) -> bool:
        return True


class Outbound:
    def __init__(self) -> None:
        self._senders: dict[str, ChannelSender] = {}

    def register(self, channels: tuple[str, ...], sender: ChannelSender) -> None:
        for c in channels:
            self._senders[c] = sender

    def can_send(self, channel: str) -> bool:
        return channel in self._senders

    async def send(self, channel: str, recipient: str, text: str, extra: dict | None = None) -> bool:
        sender = self._senders.get(channel)
        if sender is None:
            return False
        return await sender.send(channel, recipient, text, extra)

    async def send_template(self, channel: str, recipient: str, name: str, lang: str) -> bool:
        sender = self._senders.get(channel)
        fn = getattr(sender, "send_template", None)
        return bool(fn and await fn(channel, recipient, name, lang))
