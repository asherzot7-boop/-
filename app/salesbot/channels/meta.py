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
    # file: {"kind": document|photo|audio, "url": ... (Instagram/Messenger) or "media_id": ... (WhatsApp), "name", "mime"}
    attachment: dict | None = None
    comment_id: str | None = None  # Instagram comment: answer privately in Direct + a short public reply


_IG_KIND = {"image": "photo", "file": "document", "audio": "audio", "video": "document"}
_WA_KIND = {"image": "photo", "document": "document", "audio": "audio", "video": "document"}


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
                sender = (ev.get("sender") or {}).get("id")
                if msg.get("is_echo") or not sender:
                    continue
                if msg.get("text"):
                    out.append(Incoming(channel, str(sender), str(msg["text"]), msg.get("mid")))
                for i, att in enumerate(msg.get("attachments") or []):
                    kind = _IG_KIND.get(att.get("type"))
                    url = (att.get("payload") or {}).get("url")
                    if kind and url:
                        mid = f"{msg.get('mid')}:{i}" if msg.get("mid") else None
                        out.append(Incoming(channel, str(sender), "", mid, attachment={"kind": kind, "url": url, "name": None, "mime": None}))
            if obj == "instagram":
                own = str(entry.get("id") or "")
                for change in entry.get("changes") or []:
                    v = change.get("value") or {}
                    who = v.get("from") or {}
                    if change.get("field") != "comments" or not v.get("id") or not v.get("text") or not who.get("id"):
                        continue
                    if str(who["id"]) == own:
                        continue  # our own reply under the post
                    out.append(Incoming("instagram", str(who["id"]), str(v["text"]), f"comment:{v['id']}", who.get("username"),
                                        comment_id=str(v["id"])))
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
                    elif msg.get("type") in _WA_KIND and msg.get("from"):
                        m = msg.get(msg["type"]) or {}
                        if m.get("id"):
                            out.append(Incoming("whatsapp", str(msg["from"]), m.get("caption") or "", msg.get("id"), names.get(msg["from"]),
                                                "+" + str(msg["from"]), attachment={"kind": _WA_KIND[msg["type"]], "media_id": m["id"],
                                                                                   "name": m.get("filename"), "mime": m.get("mime_type")}))
                        continue
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

    async def download(self, item: Incoming, max_bytes: int) -> tuple[bytes | None, bool]:
        """(bytes, too_big). WhatsApp media needs two authorised calls; Instagram gives a direct URL."""
        att = item.attachment or {}
        try:
            if att.get("media_id"):
                token = self._secret(self.s.whatsapp_access_token)
                if not token:
                    return None, False
                headers = {"Authorization": f"Bearer {token}"}
                meta = await self._http().get(f"{self.s.meta_graph_url.rstrip('/')}/{att['media_id']}", headers=headers)
                info = meta.json() if meta.status_code < 400 else {}
                if int(info.get("file_size") or 0) > max_bytes:
                    return None, True
                if not info.get("url"):
                    return None, False
                resp = await self._http().get(info["url"], headers=headers)
            elif att.get("url"):
                resp = await self._http().get(att["url"])
            else:
                return None, False
        except Exception as exc:
            log.warning("Meta файли юклаб олинмади: %s", type(exc).__name__)
            return None, False
        if resp.status_code >= 400:
            return None, False
        if len(resp.content) > max_bytes:
            return None, True
        if not att.get("mime"):
            att["mime"] = resp.headers.get("content-type")
        return resp.content, False

    async def _post(self, url: str, body: dict, token: str, what: str) -> bool:
        try:
            resp = await self._http().post(url, json=body, headers={"Authorization": f"Bearer {token}"})
        except Exception as exc:
            log.warning("Meta API хатоси (%s): %s", what, type(exc).__name__)
            return False
        if resp.status_code >= 400:
            log.warning("Meta API рад этди (%s): %s %s", what, resp.status_code, resp.text[:300])
            return False
        return True

    async def private_reply(self, comment_id: str, text: str) -> bool:
        """Direct message to the author of a comment (Instagram allows one, within 7 days of the comment)."""
        token = self._secret(self.s.instagram_access_token)
        if not token:
            return False
        return await self._post(f"{self.s.meta_graph_url.rstrip('/')}/me/messages",
                                {"recipient": {"comment_id": comment_id}, "message": {"text": text[:1000]}}, token, "private reply")

    async def comment_reply(self, comment_id: str, text: str) -> bool:
        token = self._secret(self.s.instagram_access_token)
        if not token:
            return False
        return await self._post(f"{self.s.meta_graph_url.rstrip('/')}/{comment_id}/replies", {"message": text[:300]}, token, "comment reply")

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
