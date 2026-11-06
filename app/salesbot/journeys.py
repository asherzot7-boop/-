"""Demo journeys: three real client conversations run through the agent, rendered as web pages.

`python -m app.main visual` runs the scenarios below on a throw-away SQLite database
(no keys, nothing is sent anywhere), records every message, manager card and
payment step, and writes visual/journeys.html and visual/journeys-3d.html from
the templates in visual/templates/. The pages therefore always show what the
current agent really answers.
"""
from __future__ import annotations

import base64
import json
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
VISUAL = ROOT / "visual"


class _Recorder:
    def __init__(self):
        self.events: list[dict] = []

    def add(self, who: str, text: str, kind: str = "msg") -> None:
        self.events.append({"who": who, "text": text, "kind": kind})


class _ClientChannel:
    """Everything the client receives outside the direct reply (pushes, PDFs, templates)."""

    def __init__(self, rec: _Recorder):
        self.rec = rec

    async def send(self, channel, to, text, extra=None):
        self.rec.add("agent", text, "push")
        return True

    async def send_document(self, channel, to, data, name, caption, extra=None):
        self.rec.add("agent", name, "file")
        return True

    async def send_template(self, channel, to, name, lang):
        self.rec.add("agent", f"[шаблон {name}]", "push")
        return True


class _ManagerTelegram:
    def __init__(self, rec: _Recorder):
        self.rec = rec

    async def send(self, tid, text):
        self.rec.add("manager_in", text, "card")

    async def send_file(self, tid, data, name, caption):
        self.rec.add("manager_in", f"{caption}\n📎 {name}", "card")


async def record() -> dict[str, list[dict]]:
    from fastapi.testclient import TestClient

    from app.api.routes import create_api
    from app.config import Settings
    from app.database.models import Base, utcnow
    from app.database.session import make_engine, make_session_factory
    from app.salesbot import media
    from app.salesbot.agent import SalesAgent
    from app.salesbot.models import Lead
    from app.salesbot.outbound import Outbound

    tmp = Path(tempfile.mkdtemp(prefix="namtour-demo-"))
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp}/demo.db",
                  sales_catalog_path=str(ROOT / "sales_catalog.json"), sales_faq_path=str(ROOT / "sales_faq.json"),
                  sales_catalog_from_books=False, sales_shop_name="Jihan Jan Jasur Nam Tour", sales_files_dir=str(tmp / "files"),
                  sales_files_key=media.new_key(), sales_payment_provider="payme", payme_merchant_id="NAMTOUR", payme_key="demo",
                  sales_usd_rate=12800, sales_payment_card="8600 **** **** 1234", sales_manager_ids=[1], director_ids=[1],
                  sales_company_inn="305000000", sales_company_address="Toshkent sh.", api_token="demo",
                  openai_api_key=None, anthropic_api_key=None)
    engine = make_engine(st.database_url)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    sf = make_session_factory(engine)
    rec = _Recorder()
    out = Outbound()
    out.register(("telegram", "instagram", "whatsapp"), _ClientChannel(rec))
    agent = SalesAgent(sf, st, notifier=_ManagerTelegram(rec), outbound=out)
    api = TestClient(create_api(st, sf, sales=agent))
    n = 0

    async def say(ch, who, text, **kw):
        nonlocal n
        n += 1
        rec.add("client", text or "/start")
        r = await agent.handle(ch, who, text, message_id=f"m{n}", **kw)
        if r.text:
            rec.add("agent", r.text)

    async def send_file(ch, who, name, mime="application/pdf"):
        nonlocal n
        n += 1
        rec.add("client", name, "file")
        r = await agent.handle_file(ch, who, kind="photo" if mime.startswith("image") else "document", data=b"%PDF-demo",
                                    file_name=name, mime=mime, message_id=f"f{n}")
        if r.text:
            rec.add("agent", r.text)

    async def lead_id(who):
        async with sf() as s:
            return (await s.scalars(select(Lead).where(Lead.external_id == who).order_by(Lead.id.desc()))).first().id

    def system(text):
        rec.add("system", text, "sys")

    def manager(text):
        rec.add("manager", text, "cmd")

    result: dict[str, list[dict]] = {}
    try:
        # 1. Passport check -> a ban is found -> the agent offers deport removal
        rec.events = []
        w = "tg-anvar"
        system("Клиент нашёл бота по ссылке из Instagram-профиля (t.me/…?start=ig_bio) — источник сохранён")
        await say("telegram", w, "", source={"type": "link", "id": "ig_bio"})
        for line in ("Pasportimni tekshirish kerak, Turkiyaga kira olamanmi?", "1", "Ismim Anvar", "+998 90 555 12 34",
                     "Iloji boricha tezroq, O'zbekiston pasporti"):
            await say("telegram", w, line)
        await send_file("telegram", w, "pasport.jpg", "image/jpeg")
        await say("telegram", w, "Roziman")
        await send_file("telegram", w, "pasport.jpg", "image/jpeg")
        await say("telegram", w, "ha")
        lid = await lead_id(w)
        system("12 соатдан кейин менежер натижани текширди: тақиқ бор")
        manager(f"/result {lid} ban 2024-yil, 3 yilga")
        _, info = await agent.send_result(lid, "ban", "2024-yil, 3 yilga")
        rec.add("manager_in", info, "card")
        for line in ("ha", "2024-yil iyul, 3 yilga", "ha"):
            await say("telegram", w, line)
        result["passport"] = rec.events

        # 2. Deport removal from an Instagram ad, paid by Payme, statuses, review
        rec.events = []
        w = "ig-dilnoza"
        system("Клиент нажал на рекламу «Deportni olib tashlash» в Instagram — источник: реклама")
        await say("instagram", w, "Assalomu alaykum, deportim bor 2 yillik. Narxi qancha?",
                  source={"type": "ad", "id": "1203", "name": "Deport reklamasi"})
        for line in ("Kafolat berasizmi?", "Ismim Dilnoza", "+998 93 777 88 99", "2025-yil yanvar, 2 yilga", "ha"):
            await say("instagram", w, line)
        lid = await lead_id(w)
        async with sf() as s:
            uzs = (await s.get(Lead, lid)).extra["pay_uzs"]
        system(f"Клиент оплатил 600 $ ≈ {uzs:,} сўм по ссылке Payme".replace(",", " "))
        auth = {"Authorization": "Basic " + base64.b64encode(b"Paycom:demo").decode()}
        account = {"order_id": str(lid)}
        for method, params in (("CheckPerformTransaction", {"amount": uzs * 100, "account": account}),
                               ("CreateTransaction", {"id": "pm-1", "time": int(time.time() * 1000), "amount": uzs * 100, "account": account}),
                               ("PerformTransaction", {"id": "pm-1"})):
            r = api.post("/webhooks/payme", headers=auth, json={"method": method, "params": params, "id": 1}).json()
            ok = "✓ " + json.dumps(r["result"], ensure_ascii=False)[:60] if "result" in r else "ошибка"
            system(f"Payme → {method}: {ok}")
        for step, note in ((1, ""), (2, "Hujjatlar migratsiya xizmatiga topshirildi"), (3, ""), (4, "Deport olib tashlandi")):
            manager(f"/status {lid} {step + 1} {note}".strip())
            _, info = await agent.set_status(lid, step, note)
            rec.add("manager_in", info, "card")
        await say("instagram", w, "5")
        result["deport"] = rec.events

        # 3. Work documents via a Turkish employer on WhatsApp: checklist, reminder, status, weekly report
        rec.events = []
        w = "998911112233"
        await say("whatsapp", w, "Salom, Turkiyadagi boshlig'im hujjat yubora oladi. Qanday hujjatlar kerak?",
                  phone="+998911112233", display_name="Sardor")
        for line in ("1 kishi", "Istanbul, Kadıköy. Ha, yubora oladi", "ha"):
            await say("whatsapp", w, line)
        lid = await lead_id(w)
        await say("whatsapp", w, "Roziman")
        await send_file("whatsapp", w, "vergi_levhasi.pdf")
        await send_file("whatsapp", w, "imza_sirkuleri.pdf")
        system("Клиент пропал на 21 час — агент сам напомнил о недостающих документах")
        async with sf() as s:
            (await s.get(Lead, lid)).last_message_at = utcnow() - timedelta(hours=21)
            await s.commit()
        await agent.remind_stale()
        for name in ("oda_sicil_kaydi.pdf", "gelir_beyannamesi.pdf", "isletme_belgesi.pdf"):
            await send_file("whatsapp", w, name)
        await say("whatsapp", w, "arizam qayerda?")
        manager(f"/status {lid} 3")
        _, info = await agent.set_status(lid, 2, "")
        rec.add("manager_in", info, "card")
        system("Понедельник 09:00 — руководителю пришёл недельный отчёт")
        rec.add("manager_in", await agent.weekly_report(), "card")
        result["work"] = rec.events
    finally:
        await engine.dispose()
    for evs in result.values():
        for e in evs:
            e["tag"] = _tag(e)
    return result


def _tag(e: dict) -> str | None:
    """Which agent feature an event demonstrates (shown as a badge on the page)."""
    t, who, kind = e["text"], e["who"], e["kind"]
    rules = [
        (kind == "file" and who == "agent", "Договор и счёт PDF"),
        ("Rozimisiz" in t or ("Roziman" in t and who == "client"), "Согласие"),
        ("Hujjat qabul qilindi" in t, "Чек-лист документов"),
        ("yetishmayapti" in t, "Напоминание"),
        ("Payme" in t or "To'lovingiz qabul" in t, "Payme"),
        ("holati:" in t, "Статус заявки"),
        ("baholang" in t or ("NT" in t and "kod" in t), "Отзыв и реферальный код"),
        ("Bizda «Deportni" in t, "Допродажа"),
        ("kafolat bera olmaydi" in t, "Без обещаний гарантий"),
        ("12 soat ichida chiqadi" in t or "odatda kerak" in t, "База знаний"),
        ("Ҳафталик" in t, "Недельный отчёт"),
        (who == "system" and ("реклам" in t or "ссылке" in t), "Источник рекламы"),
        ("Янги ариза" in t, "Карточка менеджеру"),
        ("Arizangizni tekshiring" in t, "Подтверждение"),
        (kind == "cmd", "Команда менеджера"),
    ]
    return next((name for cond, name in rules if cond), None)


def render(events: dict, out_dir: Path = VISUAL) -> list[Path]:
    data = json.dumps(events, ensure_ascii=False).replace("</", "<\\/")
    written = []
    for name in ("journeys.html", "journeys-3d.html"):
        html = (VISUAL / "templates" / name).read_text(encoding="utf-8").replace("__DATA__", data)
        path = out_dir / name
        path.write_text(html, encoding="utf-8")
        written.append(path)
    return written


async def build(out_dir: Path = VISUAL) -> list[Path]:
    return render(await record(), out_dir)
