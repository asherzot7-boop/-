"""Setup page: /setup (form) and /api/setup (read and save settings, X-API-Token required).

The client fills in bot tokens, manager IDs, payment and channel keys in the
browser instead of editing .env. Secret values are never sent back to the page:
it only learns whether each one is set. An empty secret field keeps the stored
value. Everything is validated with Settings before it is written, and the
agent restarts itself when it runs under the desktop launcher.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError

from app import envfile
from app.api.routes import require_token
from app.config import Settings

PAGE = Path(__file__).with_name("setup.html")

# key, kind (text | secret | select:<options>), group, label, hint
FIELDS: list[tuple[str, str, str, str, str]] = [
    ("SALES_SHOP_NAME", "text", "company", "Kompaniya nomi", "Mijozga shu nom bilan ko‘rinadi"),
    ("SALES_BOT_TOKEN", "secret", "telegram", "Mijozlar boti tokeni", "@BotFather → /newbot. Mijozlar shu botga yozadi"),
    ("BOT_TOKEN", "secret", "telegram", "Xodimlar boti tokeni", "Ikkinchi bot: menejer buyruqlari (/leads, /reply …) shu yerda"),
    ("DIRECTOR_IDS", "text", "telegram", "Rahbar Telegram ID", "@userinfobot ga yozing — u ID raqamingizni aytadi"),
    ("SALES_MANAGER_IDS", "text", "telegram", "Menejerlar Telegram ID", "Vergul bilan: 123456789, 987654321"),
    ("ANTHROPIC_API_KEY", "secret", "ai", "Claude kaliti", "console.anthropic.com → API Keys. Agentning «aqli»"),
    ("OPENAI_API_KEY", "secret", "ai", "OpenAI kaliti (ixtiyoriy)", "Ovozli xabarlarni matnga aylantirish uchun"),
    ("SALES_PAYMENT_PROVIDER", "select:card,payme,click,none", "payment", "To‘lov usuli", "card — kartaga o‘tkazma, payme/click — avtomatik"),
    ("SALES_PAYMENT_CARD", "text", "payment", "Karta raqami va egasi", "8600 1234 5678 9012 (Jasur N.)"),
    ("SALES_USD_RATE", "text", "payment", "1 $ necha so‘m", "Payme va Click havolasi uchun, masalan 12800"),
    ("PAYME_MERCHANT_ID", "text", "payment", "Payme kassa ID", "Payme biznes kabinetidan"),
    ("PAYME_KEY", "secret", "payment", "Payme kalit", "Kassa sozlamalaridagi maxfiy kalit"),
    ("CLICK_SERVICE_ID", "text", "payment", "Click service_id", ""),
    ("CLICK_MERCHANT_ID", "text", "payment", "Click merchant_id", ""),
    ("CLICK_SECRET_KEY", "secret", "payment", "Click secret key", ""),
    ("SALES_PUBLIC_BASE_URL", "text", "meta", "Tashqi manzil", "Cloudflare Tunnel manzili, masalan https://agent.namtour.uz"),
    ("META_VERIFY_TOKEN", "secret", "meta", "Meta verify token", "Webhook ulashda o‘zingiz o‘ylab topgan so‘z"),
    ("META_APP_SECRET", "secret", "meta", "Meta App Secret", "developers.facebook.com → ilova → Settings → Basic"),
    ("INSTAGRAM_ACCESS_TOKEN", "secret", "meta", "Instagram token", ""),
    ("WHATSAPP_ACCESS_TOKEN", "secret", "meta", "WhatsApp token", ""),
    ("WHATSAPP_PHONE_NUMBER_ID", "text", "meta", "WhatsApp Phone number ID", ""),
    ("MESSENGER_PAGE_TOKEN", "secret", "meta", "Messenger sahifa tokeni", ""),
    ("TWILIO_AUTH_TOKEN", "secret", "phone", "Twilio Auth Token", "Telefon qo‘ng‘iroqlari uchun"),
    ("AZURE_SPEECH_KEY", "secret", "phone", "Azure Speech kaliti", "O‘zbekcha ovoz uchun"),
    ("AZURE_SPEECH_REGION", "text", "phone", "Azure region", "masalan westeurope"),
]
GROUPS = {"company": "Kompaniya", "telegram": "Telegram", "ai": "Sun’iy intellekt", "payment": "To‘lov",
          "meta": "Instagram, WhatsApp, Messenger", "phone": "Telefon qo‘ng‘iroqlari"}
_KIND = {k: kind for k, kind, *_ in FIELDS}

page = APIRouter()
api = APIRouter(prefix="/api/setup", dependencies=[Depends(require_token)])


def env_path(request: Request) -> Path:
    return Path(getattr(request.app.state, "env_path", None) or ".env")


def channels(values: dict[str, str]) -> dict[str, bool]:
    """What the agent can already do with these values (shown as ticks on the page)."""
    has = lambda *keys: all(values.get(k) for k in keys)
    return {"telegram": has("SALES_BOT_TOKEN"), "manager": has("BOT_TOKEN") and bool(values.get("SALES_MANAGER_IDS") or values.get("DIRECTOR_IDS")),
            "ai": has("ANTHROPIC_API_KEY") or has("OPENAI_API_KEY"), "instagram": has("INSTAGRAM_ACCESS_TOKEN", "META_APP_SECRET"),
            "whatsapp": has("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "META_APP_SECRET"),
            "payment": values.get("SALES_PAYMENT_PROVIDER", "card") == "none" or has("SALES_PAYMENT_CARD") or has("PAYME_MERCHANT_ID", "PAYME_KEY")
            or has("CLICK_SERVICE_ID", "CLICK_SECRET_KEY"), "phone": has("TWILIO_AUTH_TOKEN")}


@page.get("/setup", response_class=HTMLResponse)
async def setup_page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@api.get("")
async def get_setup(request: Request) -> dict:
    values = envfile.read(env_path(request))
    fields = []
    for key, kind, group, label, hint in FIELDS:
        f = {"key": key, "kind": kind.split(":")[0], "group": group, "label": label, "hint": hint}
        if kind == "secret":
            f["set"] = bool(values.get(key))
        else:
            f["value"] = values.get(key, "")
        if kind.startswith("select:"):
            f["options"] = kind.split(":", 1)[1].split(",")
        fields.append(f)
    return {"groups": GROUPS, "fields": fields, "channels": channels(values)}


class SetupIn(BaseModel):
    values: dict[str, str]


@api.post("")
async def save_setup(body: SetupIn, request: Request) -> dict:
    unknown = sorted(set(body.values) - set(_KIND))
    if unknown:
        raise HTTPException(400, f"Noma’lum sozlama: {', '.join(unknown)}")
    changes: dict[str, str] = {}
    for key, value in body.values.items():
        value = value.strip()
        if "\n" in value or "\r" in value:
            raise HTTPException(400, f"{key}: bitta qatorda yozing")
        if _KIND[key] == "secret" and not value:
            continue  # an empty secret field keeps what is stored
        if _KIND[key].startswith("select:") and value not in _KIND[key].split(":", 1)[1].split(","):
            raise HTTPException(400, f"{key}: noto‘g‘ri qiymat")
        changes[key] = value
    path = env_path(request)
    merged = {**envfile.read(path), **changes}
    # check the file exactly as the agent will read it: a draft copy with the changes, loaded by Settings
    draft = path.with_name(path.name + ".check")
    try:
        if path.exists():
            shutil.copyfile(path, draft)
        envfile.update(draft, changes)
        Settings(_env_file=str(draft))
    except ValidationError as e:
        bad = ", ".join(sorted({str(err["loc"][0]).upper() for err in e.errors() if err.get("loc")})) or "qiymat"
        raise HTTPException(400, f"Tekshiring: {bad}") from None
    finally:
        draft.unlink(missing_ok=True)
    if changes:
        envfile.update(path, changes)
    restarting = bool(changes) and os.environ.get("NAMTOUR_SUPERVISED") == "1"
    if restarting:  # the launcher starts the agent again with the new settings
        asyncio.get_running_loop().call_later(1.5, os._exit, 3)
    return {"saved": sorted(changes), "restarting": restarting, "channels": channels(merged)}
