"""Reklama manbasi, eski mijozlarga xabar va sayt chati."""
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import create_api
from app.database.models import utcnow
from app.notify import ConsoleNotifier
from app.salesbot.agent import SalesAgent
from app.salesbot.channels import meta
from app.salesbot.models import Lead
from app.salesbot.outbound import Outbound, WebSender
from tests.conftest import build_env, make_settings

ROOT = Path(__file__).resolve().parents[1]


class Sink:
    def __init__(self):
        self.sent, self.templates = [], []

    async def send(self, channel, recipient, text, extra=None):
        self.sent.append((channel, recipient, text))
        return True

    async def send_template(self, channel, recipient, name, lang):
        self.templates.append((recipient, name, lang))
        return True


@pytest.fixture
async def env3(tmp_path):
    settings = make_settings(tmp_path, api_token="t", sales_catalog_path=str(ROOT / "sales_catalog.json"), sales_faq_path=str(ROOT / "sales_faq.json"),
                             sales_catalog_from_books=False, sales_manager_ids=[777], sales_consent_required=False,
                             whatsapp_reengage_template="namtour_qayta", sales_source_names={"120330": "Deport reklamasi, noyabr"})
    e = await build_env(settings)
    e.sink = Sink()
    out = Outbound(); out.register(("telegram", "instagram", "whatsapp"), e.sink); out.register(("web",), WebSender())
    e.agent = SalesAgent(e.sf, settings, notifier=ConsoleNotifier(), outbound=out)
    e.service.sales = e.agent
    e.client = TestClient(create_api(settings, e.sf, sales=e.agent))
    yield e
    await e.engine.dispose()


def test_meta_referrals_are_parsed():
    wa = meta.parse({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [
        {"from": "99890", "id": "w1", "type": "text", "text": {"body": "Salom, deport narxi?"},
         "referral": {"source_type": "ad", "source_id": "120330", "headline": "Deportni olib tashlash", "source_url": "https://fb.me/x"}}]}}]}]})
    assert wa[0].source == {"type": "ad", "id": "120330", "name": "Deportni olib tashlash", "url": "https://fb.me/x"}
    ig = meta.parse({"object": "instagram", "entry": [{"messaging": [
        {"sender": {"id": "5"}, "timestamp": 1, "referral": {"ad_id": "777", "source": "ADS", "ads_context_data": {"ad_title": "Pasport"}}}]}]})
    assert ig[0].source == {"type": "ad", "id": "777", "name": "Pasport"} and ig[0].text == ""


async def test_source_is_kept_and_reported(env3):
    a = env3.agent
    src = {"type": "ad", "id": "120330", "name": "Deportni olib tashlash"}
    for i, line in enumerate(["Deportim bor 3 yillik, narxi qancha?", "Ismim Bek", "+998901112233", "2023, 3 yil", "ha"]):
        await a.handle("whatsapp", "99890", line, message_id=f"x{i}", source=src if i == 0 else {"type": "ad", "id": "other"})
    await a.handle("telegram", "1", "", source={"type": "link", "id": "ig_bio"})
    await a.handle("instagram", "2", "Salom")
    d = env3.client.get("/api/panel/director", headers={"X-API-Token": "t"}).json()
    rows = {r["name"]: r for r in d["by_source"]}
    assert rows["Реклама: Deport reklamasi, noyabr"] == {"name": "Реклама: Deport reklamasi, noyabr", "leads": 1, "won": 1, "revenue": "2 600 $"}
    assert rows["Ҳавола: ig_bio"]["leads"] == 1 and rows["Органик (ўзи ёзди)"]["leads"] == 1


async def test_reengagement_respects_channel_rules(env3):
    a = env3.agent
    for ch, who in (("telegram", "t1"), ("whatsapp", "998901"), ("instagram", "i1")):
        await a.handle(ch, who, "Deport narxi qancha?", message_id=f"{who}-1")
    async with env3.sf() as s:
        for lead in await s.scalars(select(Lead)):
            lead.last_message_at = utcnow() - timedelta(days=30)
        await s.commit()
    assert await a.reengage() == 2
    assert [x[1] for x in env3.sink.sent] == ["t1"] and "savolingiz hal bo'ldimi (Deportni olib tashlash" in env3.sink.sent[0][2]
    assert env3.sink.templates == [("998901", "namtour_qayta", "uz")]
    assert await a.reengage() == 0  # only once


async def test_site_chat_widget_flow(env3):
    c = env3.client
    assert "attachShadow" in c.get("/webhooks/web/widget.js").text
    r = c.post("/webhooks/web/chat", json={"session": "abcDEF123456", "text": "", "n": 1, "source": {"source": "google", "campaign": "deport"}})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    greet = r.json()
    assert greet["reply"].startswith("Assalomu alaykum") and greet["last_id"] > 0
    r = c.post("/webhooks/web/chat", json={"session": "abcDEF123456", "text": "Pasport tekshirish narxi qancha?", "n": 2}).json()
    assert "80 $" in r["reply"]
    ok, _ = await env3.agent.manager_reply(1, "Ofisga ham kelishingiz mumkin")
    assert ok
    polled = c.get(f"/webhooks/web/poll?session=abcDEF123456&after={r['last_id']}").json()
    assert [m["text"] for m in polled] == ["👤 Menejer: Ofisga ham kelishingiz mumkin"]
    async with env3.sf() as s:
        lead = await s.scalar(select(Lead).where(Lead.channel == "web"))
        assert lead.extra["source"] == {"type": "link", "id": "deport", "name": "google"}
    assert c.post("/webhooks/web/chat", json={"session": "../x", "text": "hi"}).status_code == 400
    codes = [c.post("/webhooks/web/chat", json={"session": "flood1234567", "text": "salom"}).status_code for _ in range(32)]
    assert codes[-1] == 429 and codes[0] == 200
