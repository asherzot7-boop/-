"""Numbers for the four panels: connections, director, agent process, manager.

Everything is computed from the database and settings; no secret value
ever leaves this module, only whether it is set.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import utcnow
from app.salesbot import catalog as cat
from app.salesbot.agent import CHANNEL_NAMES
from app.salesbot.models import OPEN_STAGES, FaqEntry, Lead, LeadFile, LeadMessage, LeadStage

STAGE_NAMES = {
    LeadStage.NEW: "Янги", LeadStage.NEED: "Эҳтиёж", LeadStage.QUANTITY: "Хизмат танланди", LeadStage.NAME: "Исм",
    LeadStage.PHONE: "Телефон", LeadStage.ADDRESS: "Тафсилот", LeadStage.CONFIRM: "Тасдиқ", LeadStage.WON: "Ариза ✓",
    LeadStage.HANDOFF: "Менежерда", LeadStage.LOST: "Рад этди",
}
FUNNEL = [LeadStage.NEED, LeadStage.QUANTITY, LeadStage.NAME, LeadStage.PHONE, LeadStage.ADDRESS, LeadStage.CONFIRM, LeadStage.WON]
LANG_NAMES = {"uz": "Ўзбек (лотин)", "uzc": "Ўзбек (кирилл)", "tr": "Турк", "ru": "Рус"}


def _set(secret) -> bool:
    if secret is None:
        return False
    value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    return bool(value)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


# ------------------------------------------------------------------ connections
async def connections(session: AsyncSession, settings, now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    last_in = dict((await session.execute(
        select(LeadMessage.channel, func.max(LeadMessage.created_at)).where(LeadMessage.direction == "in").group_by(LeadMessage.channel)
    )).all())
    public = bool(settings.sales_public_base_url)
    meta_base = _set(settings.meta_app_secret) and _set(settings.meta_verify_token)

    def item(key, title, group, needed: dict[str, bool], hint: str, channels=(), detail=""):
        have = sum(needed.values())
        status = "ok" if have == len(needed) else ("partial" if have else "off")
        last = max((last_in.get(c) for c in channels if last_in.get(c)), default=None)
        return {"key": key, "title": title, "group": group, "status": status, "detail": detail,
                "missing": [k for k, v in needed.items() if not v], "hint": hint if status != "ok" else "", "last_activity": _iso(last)}

    out = [
        item("tg_client", "Telegram: мижозлар боти", "Мижоз каналлари", {"SALES_BOT_TOKEN": _set(settings.sales_bot_token)},
             "@BotFather'да бот яратинг ва токенни .env га ёзинг", ("telegram", "telegram_business")),
        item("instagram", "Instagram Direct ва изоҳлар", "Мижоз каналлари",
             {"INSTAGRAM_ACCESS_TOKEN": _set(settings.instagram_access_token), "META_APP_SECRET": _set(settings.meta_app_secret),
              "META_VERIFY_TOKEN": _set(settings.meta_verify_token), "SALES_PUBLIC_BASE_URL": public},
             "Meta App, Instagram токени ва Cloudflare Tunnel керак", ("instagram",),
             f"Изоҳлар: {settings.sales_comment_mode}"),
        item("whatsapp", "WhatsApp Business", "Мижоз каналлари",
             {"WHATSAPP_ACCESS_TOKEN": _set(settings.whatsapp_access_token), "WHATSAPP_PHONE_NUMBER_ID": bool(settings.whatsapp_phone_number_id),
              "META_APP_SECRET": meta_base, "SALES_PUBLIC_BASE_URL": public},
             "Meta App'га WhatsApp Cloud API қўшинг ва рақамни уланг", ("whatsapp",)),
        item("messenger", "Facebook Messenger", "Мижоз каналлари",
             {"MESSENGER_PAGE_TOKEN": _set(settings.messenger_page_token), "META_APP_SECRET": meta_base, "SALES_PUBLIC_BASE_URL": public},
             "Ихтиёрий: Facebook саҳифаси токени", ("messenger",)),
        item("phone", "Телефон (Twilio)", "Мижоз каналлари", {"TWILIO_AUTH_TOKEN": _set(settings.twilio_auth_token), "SALES_PUBLIC_BASE_URL": public},
             "Twilio рақами ва Cloudflare Tunnel керак", ("phone",),
             f"Нутқ: {settings.sales_voice_stt_language}, овоз: {settings.sales_voice_tts_language}"),
        item("tg_staff", "Telegram: менежерлар боти", "Жамоа",
             {"BOT_TOKEN": _set(settings.bot_token), "DIRECTOR_IDS / SALES_MANAGER_IDS": bool(settings.director_ids or settings.sales_manager_ids)},
             "Иккинчи бот ва менежерлар Telegram ID'лари", (),
             f"Менежерлар: {len(settings.sales_manager_ids) or len(settings.director_ids)} та"),
        item("ai", "AI жавоблар", "Ақл ва овоз", {"OPENAI_API_KEY": settings.llm_enabled},
             "Калитсиз агент қоидалар асосида ишлайди (бу ҳам нормал)", (),
             f"Модель: {settings.openai_model}" if settings.llm_enabled else "Қоидалар режими"),
        item("stt", "Овозли хабарни матнга", "Ақл ва овоз", {"OPENAI_API_KEY": settings.llm_enabled},
             "OpenAI калити бўлса ёқилади", (), f"Модель: {settings.sales_stt_model}"),
    ]
    provider = (settings.sales_payment_provider or "none").lower()
    pay_needed = {"card": {"SALES_PAYMENT_CARD": bool(settings.sales_payment_card)},
                  "payme": {"PAYME_MERCHANT_ID": bool(settings.payme_merchant_id), "SALES_USD_RATE": bool(settings.sales_usd_rate)},
                  "click": {"CLICK_SERVICE_ID": bool(settings.click_service_id), "CLICK_MERCHANT_ID": bool(settings.click_merchant_id),
                            "SALES_USD_RATE": bool(settings.sales_usd_rate)},
                  "link": {"SALES_PAYMENT_LINK_TEMPLATE": bool(settings.sales_payment_link_template)}}.get(provider, {"SALES_PAYMENT_PROVIDER": False})
    out.append(item("payment", "Олдиндан тўлов", "Пул ва ҳисобот", pay_needed, "Карта рақами ёки Payme/Click маълумотлари", (),
                    f"Усул: {provider}" + (f", курс {settings.sales_usd_rate:,.0f} сўм".replace(",", " ") if settings.sales_usd_rate else "")))
    out.append(item("sheets", "Google Sheets жадвали", "Пул ва ҳисобот", {"SALES_SHEETS_WEBHOOK_URL": _set(settings.sales_sheets_webhook_url)},
                    "Apps Script веб-иловаси манзили (README)", ()))
    backups = sorted(Path(settings.backup_dir).glob("agent_backup_*.zip")) if Path(settings.backup_dir).exists() else []
    last_backup = datetime.fromtimestamp(backups[-1].stat().st_mtime, UTC).replace(tzinfo=None) if backups else None
    b = item("backup", "Захира нусха", "Пул ва ҳисобот", {"BACKUP": bool(last_backup)}, "install.bat кунлик захирани ёқади", (),
             f"{len(backups)} та нусха, {settings.backup_dir}" if backups else "Ҳали нусха олинмаган")
    b["last_activity"] = _iso(last_backup)
    if last_backup and now - last_backup > timedelta(hours=36):
        b["status"], b["hint"] = "partial", "Охирги нусха 36 соатдан эски: NamTourBackup вазифасини текширинг"
    out.append(b)
    return out


# ------------------------------------------------------------------ director
def _local_day(dt: datetime, tz: ZoneInfo) -> str:
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%Y-%m-%d")


async def director(session: AsyncSession, settings, days: int = 30, now: datetime | None = None) -> dict:
    now = now or utcnow()
    since = now - timedelta(days=days)
    leads = list(await session.scalars(select(Lead).where(Lead.created_at >= since)))
    products = {p.id: p for p in await cat.load_catalog(session, settings.sales_catalog_path, settings.sales_catalog_from_books)}
    won = [x for x in leads if x.stage == LeadStage.WON]

    revenue: dict[str, int] = defaultdict(int)
    prepay_due: dict[str, int] = defaultdict(int)
    prepaid: dict[str, int] = defaultdict(int)
    for x in won:
        cur = (x.currency or (products[x.product_id].currency if x.product_id in products else "so'm"))
        revenue[cur] += x.total or 0
        if x.prepayment_due:
            prepay_due[cur] += x.prepayment_due
            if x.prepaid_at:
                prepaid[cur] += x.prepayment_due

    def group(key) -> list[dict]:
        rows: dict[str, dict] = {}
        for x in leads:
            k = key(x)
            if k is None:
                continue
            r = rows.setdefault(k, {"name": k, "leads": 0, "won": 0})
            r["leads"] += 1
            r["won"] += x.stage == LeadStage.WON
        return sorted(rows.values(), key=lambda r: (-r["leads"], r["name"]))

    tz = settings.tz
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    span = min(days, 31)
    daily = {(today - timedelta(days=i)).isoformat(): {"leads": 0, "won": 0} for i in range(span - 1, -1, -1)}
    for x in leads:
        d = _local_day(x.created_at, tz)
        if d in daily:
            daily[d]["leads"] += 1
            daily[d]["won"] += x.stage == LeadStage.WON

    reached = Counter()
    order = {s: i for i, s in enumerate(FUNNEL)}
    for x in leads:
        pos = order.get(x.stage)
        if x.stage in (LeadStage.HANDOFF, LeadStage.LOST, LeadStage.NEW):
            pos = 0
        for s in FUNNEL[: (pos or 0) + 1]:
            reached[s] += 1
    from app.database.models import AuditLog

    access = list(await session.scalars(select(AuditLog).where(AuditLog.action.in_(["file.viewed", "file.forwarded", "file.purged"]))
                                        .order_by(AuditLog.id.desc()).limit(20)))
    upsell = [x for x in leads if (x.extra or {}).get("from_lead")]
    ratings = [x.extra["rating"] for x in leads if (x.extra or {}).get("rating")]
    money = lambda d: [{"currency": c, "amount": v, "text": cat.money(v, c, "uzc")} for c, v in sorted(d.items()) if v]
    return {
        "days": days,
        "totals": {"leads": len(leads), "won": len(won), "conversion": round(100 * len(won) / len(leads)) if leads else 0,
                   "handoff": sum(x.stage == LeadStage.HANDOFF for x in leads), "lost": sum(x.stage == LeadStage.LOST for x in leads),
                   "upsell_offered": len(upsell), "upsell_won": sum(x.stage == LeadStage.WON for x in upsell),
                   "rating": round(sum(ratings) / len(ratings), 1) if ratings else None, "ratings": len(ratings),
                   "referrals": sum(1 for x in leads if (x.extra or {}).get("referred_by"))},
        "revenue": money(revenue), "prepay_due": money(prepay_due), "prepaid": money(prepaid),
        "by_channel": group(lambda x: CHANNEL_NAMES.get(x.channel, x.channel)),
        "by_service": group(lambda x: (products[x.product_id].title("uzc") if x.product_id in products else x.product_name) if x.product_id else None),
        "by_language": group(lambda x: LANG_NAMES.get(x.language, x.language)),
        "daily": [{"day": d, **v} for d, v in daily.items()],
        "funnel": [{"stage": STAGE_NAMES[s], "count": reached[s]} for s in FUNNEL],
        "access": [{"time": _iso(a.created_at), "action": {"file.viewed": "Панелда очилди", "file.forwarded": "Менежерга юборилди",
                                                            "file.purged": "Автоматик ўчирилди"}[a.action],
                    "lead_id": a.entity_id, "details": a.details or {}} for a in access],
    }


# ------------------------------------------------------------------ agent process
async def agent_process(session: AsyncSession, settings, now: datetime | None = None) -> dict:
    now = now or utcnow()
    day_start = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(settings.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = day_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    rows = (await session.execute(
        select(LeadMessage, Lead).join(Lead, Lead.id == LeadMessage.lead_id).order_by(LeadMessage.id.desc()).limit(40)
    )).all()
    feed = [{"time": _iso(m.created_at), "lead_id": ld.id, "channel": CHANNEL_NAMES.get(ld.channel, ld.channel), "who": ld.name or ld.display_name or ld.external_id,
             "direction": m.direction, "text": m.text[:300]} for m, ld in rows]
    msgs_today = dict((await session.execute(
        select(LeadMessage.direction, func.count()).where(LeadMessage.created_at >= day_start).group_by(LeadMessage.direction))).all())
    open_leads = list(await session.scalars(select(Lead).where(Lead.stage.in_(list(OPEN_STAGES)))))
    stages = Counter(x.stage for x in open_leads)
    files_today = list(await session.scalars(select(LeadFile).where(LeadFile.created_at >= day_start)))
    all_open_q = [x for x in await session.scalars(select(Lead)) if (x.extra or {}).get("open_question")]
    return {
        "mode": f"AI ({settings.openai_model})" if settings.llm_enabled else "Қоидалар режими",
        "today": {
            "in": msgs_today.get("in", 0), "out": msgs_today.get("out", 0),
            "files": sum(f.kind != "audio" for f in files_today), "voice": sum(f.kind == "audio" for f in files_today),
            "voice_understood": sum(f.kind == "audio" and bool(f.transcript) for f in files_today),
            "reminders": await session.scalar(select(func.count(Lead.id)).where(Lead.last_reminder_at >= day_start)) or 0,
            "new_leads": await session.scalar(select(func.count(Lead.id)).where(Lead.created_at >= day_start)) or 0,
            "won": await session.scalar(select(func.count(Lead.id)).where(Lead.stage == LeadStage.WON, Lead.updated_at >= day_start)) or 0,
        },
        "open_by_stage": [{"stage": STAGE_NAMES[s], "count": stages.get(s, 0)} for s in FUNNEL[:-1]],
        "waiting": {"handoff": await session.scalar(select(func.count(Lead.id)).where(Lead.stage == LeadStage.HANDOFF)) or 0,
                    "questions": len(all_open_q)},
        "faq": {"learned": await session.scalar(select(func.count(FaqEntry.id)).where(FaqEntry.active.is_(True))) or 0,
                "uses": await session.scalar(select(func.coalesce(func.sum(FaqEntry.uses), 0))) or 0},
        "feed": feed,
    }


# ------------------------------------------------------------------ manager
STATUS_UZC = ["Ҳужжатлар кутилмоқда", "Ҳужжатлар қабул қилинди", "Ариза топширилди", "Натижа кутилмоқда", "Якунланди ✅"]


def _card(x: Lead, products: dict, files: dict) -> dict:
    p = products.get(x.product_id)
    ex = x.extra or {}
    got = ex.get("docs", {})
    return {"status": ex.get("status"), "status_name": STATUS_UZC[ex["status"]] if isinstance(ex.get("status"), int) else None,
            "checklist": [{"id": d["id"], "name": cat._localized(d["name"], "uz"), "received": d["id"] in got} for d in (p.required_docs if p else [])],
            "rating": ex.get("rating"), "referred_by": ex.get("referred_by"), "source": ex.get("source"),
            **{"id": x.id, "channel": CHANNEL_NAMES.get(x.channel, x.channel), "channel_key": x.channel, "who": x.name or x.display_name or x.external_id,
            "phone": x.phone, "language": LANG_NAMES.get(x.language, x.language), "service": x.product_name, "quantity": x.quantity,
            "total": cat.price_text(p, "uzc", x.total) if p and x.total else None, "details": x.address,
            "prepay": cat.money(x.prepayment_due, x.currency or "so'm", "uzc") if x.prepayment_due else None,
            "paid": bool(x.prepaid_at), "stage": STAGE_NAMES[x.stage], "question": (x.extra or {}).get("open_question"),
            "files": files.get(x.id, []), "updated": _iso(x.last_message_at), "can_write": x.channel != "phone"}}


async def manager(session: AsyncSession, settings) -> dict:
    products = {p.id: p for p in await cat.load_catalog(session, settings.sales_catalog_path, settings.sales_catalog_from_books)}
    leads = list(await session.scalars(select(Lead).order_by(Lead.last_message_at.desc()).limit(500)))
    files: dict[int, list] = defaultdict(list)
    for f in await session.scalars(select(LeadFile).where(LeadFile.lead_id.in_([x.id for x in leads] or [0])).order_by(LeadFile.id)):
        files[f.lead_id].append({"id": f.id, "name": f.file_name, "kind": f.kind, "has_file": bool(f.path), "transcript": f.transcript})
    card = lambda x: _card(x, products, files)
    return {
        "payments": [card(x) for x in leads if x.stage == LeadStage.WON and x.prepayment_due and not x.prepaid_at],
        "results": [card(x) for x in leads if x.stage == LeadStage.WON and x.product_id in products
                    and products[x.product_id].next_offers and not (x.extra or {}).get("result")],
        "handoff": [card(x) for x in leads if x.stage == LeadStage.HANDOFF],
        "questions": [card(x) for x in leads if (x.extra or {}).get("open_question")],
        "inwork": [card(x) for x in leads if x.stage == LeadStage.WON and (x.extra or {}).get("status", 1) < 4][:30],
    }


async def lead_detail(session: AsyncSession, settings, lead_id: int) -> dict | None:
    x = await session.get(Lead, lead_id)
    if x is None:
        return None
    products = {p.id: p for p in await cat.load_catalog(session, settings.sales_catalog_path, settings.sales_catalog_from_books)}
    files = {x.id: [{"id": f.id, "name": f.file_name, "kind": f.kind, "has_file": bool(f.path), "transcript": f.transcript}
                    for f in await session.scalars(select(LeadFile).where(LeadFile.lead_id == x.id).order_by(LeadFile.id))]}
    msgs = list(await session.scalars(select(LeadMessage).where(LeadMessage.lead_id == x.id).order_by(LeadMessage.id)))
    return {**_card(x, products, files), "messages": [{"time": _iso(m.created_at), "direction": m.direction, "text": m.text} for m in msgs]}
