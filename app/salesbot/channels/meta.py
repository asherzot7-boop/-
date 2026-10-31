"""Meta webhooks: Instagram Direct, WhatsApp Cloud API and Facebook Messenger.

One webhook URL (/webhooks/meta) serves all three; the payload's "object"
field tells them apart. Every POST must carry a valid X-Hub-Signature-256
(HMAC-SHA256 of the raw body with the app secret).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Incoming:
    channel: str  # instagram / whatsapp / messenger
    sender: str
    text: str
    message_id: str | None
    name: str | None = None
    phone: str | None = None


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def parse(payload: dict) -> list[Incoming]:
    """Text messages only. Echoes of our own replies, reactions and statuses are ignored."""
    out: list[Incoming] = []
    obj = payload.get("object")
    for entry in payload.get("entry") or []:
        if obj in ("instagram", "page"):
            channel = "instagram" if obj == "instagram" else "messenger"
            for ev in entry.get("messaging") or []:
                msg = ev.get("message") or {}
                if msg.get("is_echo") or not msg.get("text"):
                    continue
                sender = (ev.get("sender") or {}).get("id")
                if sender:
                    out.append(Incoming(channel, str(sender), str(msg["text"]), msg.get("mid")))
        elif obj == "whatsapp_business_account":
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []}
                for msg in value.get("messages") or []:
                    if msg.get("type") == "text":
                        body = (msg.get("text") or {}).get("body")
                    elif msg.get("type") == "button":
                        body = (msg.get("button") or {}).get("text")
                    elif msg.get("type") == "interactive":
                        inter = msg.get("interactive") or {}
                        body = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title")
                    else:
                        body = None
                    sender = msg.get("from")
                    if sender and body:
                        out.append(Incoming("whatsapp", str(sender), str(body), msg.get("id"), names.get(sender), "+" + str(sender)))
    return out


class MetaSender:
    """Sends replies through the Graph API. Missing token -> logs and skips."""

    def __init__(self, settings, client=None) -> None:
        self.s = settings
        self._client = client

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    @staticmethod
    def _secret(v) -> str | None:
        return v.get_secret_value() if v is not None and v.get_secret_value() else None

    async def send(self, channel: str, recipient: str, text: str) -> bool:
        base = self.s.meta_graph_url.rstrip("/")
        if channel == "whatsapp":
            token, phone_id = self._secret(self.s.whatsapp_access_token), self.s.whatsapp_phone_number_id
            if not token or not phone_id:
                log.warning("WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID берилмаган: жавоб юборилмади")
                return False
            url = f"{base}/{phone_id}/messages"
            body = {"messaging_product": "whatsapp", "to": recipient, "type": "text", "text": {"body": text[:4096]}}
        else:
            token = self._secret(self.s.instagram_access_token if channel == "instagram" else self.s.messenger_page_token)
            if not token:
                log.warning("%s учун токен берилмаган: жавоб юборилмади", channel)
                return False
            url = f"{base}/me/messages"
            body = {"recipient": {"id": recipient}, "message": {"text": text[:1000]}}
        try:
            resp = await self._http().post(url, json=body, headers={"Authorization": f"Bearer {token}"})
        except Exception as exc:
            log.warning("Meta API хатоси (%s): %s", channel, type(exc).__name__)
            return False
        if resp.status_code >= 400:
            log.warning("Meta API рад этди (%s): %s %s", channel, resp.status_code, resp.text[:300])
            return False
        return True
