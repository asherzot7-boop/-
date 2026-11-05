"""Ariza holati, hujjatlar ro'yxati, baho va tavsiya kodi."""
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import create_api
from app.database.models import utcnow
from app.notify import ConsoleNotifier
from app.salesbot.agent import SalesAgent
from app.salesbot.models import Lead
from app.salesbot.outbound import Outbound
from tests.conftest import DIRECTOR, build_env, make_settings

ROOT = Path(__file__).resolve().parents[1]


class Sink:
    def __init__(self):
        self.sent = []

    async def send(self, channel, recipient, text, extra=None):
        self.sent.append((channel, recipient, text))
        return True


@pytest.fixture
async def st(tmp_path):
    settings = make_settings(tmp_path, api_token="t", sales_catalog_path=str(ROOT / "sales_catalog.json"), sales_faq_path=str(ROOT / "sales_faq.json"),
                             sales_catalog_from_books=False, sales_manager_ids=[777], sales_files_dir=str(tmp_path / "f"), sales_consent_required=False)
    e = await build_env(settings)
    e.notifier, e.sink = ConsoleNotifier(), Sink()
    out = Outbound(); out.register(("telegram", "instagram", "whatsapp"), e.sink)
    e.agent = SalesAgent(e.sf, settings, notifier=e.notifier, outbound=out)
    e.service.sales = e.agent
    yield e
    await e.engine.dispose()


async def talk(a, lines, who="w1", ch="telegram", start=0):
    return [await a.handle(ch, who, x, message_id=f"{who}-{start + i}") for i, x in enumerate(lines)]


WORK = ["Turkiyada ish beruvchim bor, ishlash uchun hujjatlar kerak", "1 kishi", "Ismim Aziz", "+998 90 123 45 67", "Istanbul, ha yubora oladi", "ha"]


async def test_checklist_follows_received_documents(st):
    r = await talk(st.agent, WORK)
    assert r[-1].won and "Qoldi: Vergi levhası" in r[-1].text
    r = await st.agent.handle_file("telegram", "w1", kind="document", data=b"%PDF", file_name="vergi_levhasi.pdf", message_id="d1")
    assert "Qabul qilingan: Vergi levhası" in r.text and "Vergi levhası" not in r.text.split("Qoldi:")[1]
    for i, (fname, cap) in enumerate([("scan1.pdf", "imza sirküleri"), ("oda_sicil.pdf", ""), ("gelir.pdf", ""), ("İşletme Belgesi.pdf", "")]):
        r = await st.agent.handle_file("telegram", "w1", kind="document", data=b"%PDF", file_name=fname, caption=cap or None, message_id=f"d{i + 2}")
    assert "Barcha hujjatlar qabul qilindi" in r.text
    async with st.sf() as s:
        lead = await s.scalar(select(Lead))
        assert set(lead.extra["docs"]) == {"vergi", "imza", "oda", "gelir", "isletme"} and lead.extra["status"] == 1


async def test_status_updates_reach_the_client_and_end_with_a_rating(st):
    await talk(st.agent, ["Здравствуйте, сколько стоит снять депорт?", "Меня зовут Анна", "+998935556677", "2024, на 3 года", "да"], who="ru1")
    reply = await st.service.dispatch(DIRECTOR, "/status 1 3 Подали в миграционную службу")
    assert "юборилди" in reply.text
    assert st.sink.sent[-1][2] == "Статус заявки №1: Заявка подана. Подали в миграционную службу"
    r = await talk(st.agent, ["Что с заявкой?"], who="ru1", start=10)
    assert r[0].text.startswith("Статус заявки №1: Заявка подана")
    await st.service.dispatch(DIRECTOR, "/status 1 5")
    assert "Оцените нашу работу от 1 до 5" in st.sink.sent[-1][2]
    r = await talk(st.agent, ["5"], who="ru1", start=20)
    assert "«NT1»" in r[0].text
    r = await talk(st.agent, ["Привет, мне друг дал код NT1, сколько стоит проверка паспорта?"], who="ru2")
    async with st.sf() as s:
        new = await s.scalar(select(Lead).where(Lead.external_id == "ru2"))
        assert new.extra["referred_by"] == 1
        assert (await s.get(Lead, 1)).extra["rating"] == 5
    assert any("Тавсия коди" in t for _, t in st.notifier.sent)


async def test_low_rating_alerts_the_manager(st):
    await talk(st.agent, ["Deport narxi qancha?", "Ismim Bek", "+998901112233", "2023, 2 yil", "ha"], who="b1")
    await st.agent.set_status(1, 4)
    await talk(st.agent, ["2"], who="b1", start=10)
    assert any("Паст баҳо: 2/5" in t for _, t in st.notifier.sent)


async def test_missing_document_reminder(st):
    await talk(st.agent, WORK)
    async with st.sf() as s:
        lead = await s.scalar(select(Lead))
        lead.last_message_at = utcnow() - timedelta(hours=21)
        await s.commit()
    assert await st.agent.remind_stale() == 1
    assert "hujjatlar yetishmayapti: Vergi levhası" in st.sink.sent[-1][2]
    assert await st.agent.remind_stale() == 0  # once a day at most


async def test_panel_status_and_manual_checklist(st):
    await talk(st.agent, WORK)
    c = TestClient(create_api(st.settings, st.sf, sales=st.agent))
    h = {"X-API-Token": "t"}
    m = c.get("/api/panel/manager", headers=h).json()
    card = m["inwork"][0]
    assert card["status_name"] == "Ҳужжатлар кутилмоқда" and len(card["checklist"]) == 5
    assert c.post(f"/api/panel/leads/{card['id']}/docs", headers=h, json={"doc_id": "oda", "received": True}).json()["ok"]
    assert c.post(f"/api/panel/leads/{card['id']}/status", headers=h, json={"step": 2, "note": ""}).json()["ok"]
    card = c.get("/api/panel/manager", headers=h).json()["inwork"][0]
    assert card["status"] == 2 and [d["received"] for d in card["checklist"]].count(True) == 1
    assert "Ariza topshirildi" in st.sink.sent[-1][2]
