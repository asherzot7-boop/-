"""Konsultatsiyaga yozilish, menejerlarga taqsimlash va javob vaqti nazorati."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

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


class FakeHttp:
    def __init__(self):
        self.posts = []

    async def post(self, url, json):
        self.posts.append((url, json))

        class R:
            status_code = 200
        return R()


@pytest.fixture
async def env4(tmp_path):
    settings = make_settings(tmp_path, sales_catalog_path=str(ROOT / "sales_catalog.example.json"), sales_faq_path=None, sales_catalog_from_books=False,
                             sales_consent_required=False, sales_booking_days="1-7", sales_booking_hours="09:00-21:00",
                             sales_calendar_webhook_url="https://script.google.com/macros/s/CAL/exec",
                             sales_routing={"tr": [111], "tur-istanbul": [222], "default": [333]}, sales_sla_minutes=15)
    e = await build_env(settings)
    e.notifier, e.sink = ConsoleNotifier(), Sink()
    out = Outbound(); out.register(("telegram", "instagram", "whatsapp"), e.sink)
    e.agent = SalesAgent(e.sf, settings, notifier=e.notifier, outbound=out)
    e.agent.http = FakeHttp()
    e.service.sales = e.agent
    yield e
    await e.engine.dispose()


async def talk(a, lines, who="c1", ch="telegram", start=0):
    return [await a.handle(ch, who, x, message_id=f"{who}-{start + i}") for i, x in enumerate(lines)]


async def test_consultation_offers_free_slots_and_books_one(env4):
    r = await talk(env4.agent, ["Здравствуйте, нужна консультация по визе", "Анна", "+998935556677"], who="a")
    assert "Выберите удобное время" in r[2].text and "1) " in r[2].text and "3) " in r[2].text
    first_slot = r[2].text.split("1) ")[1].split("\n")[0]
    r = await talk(env4.agent, ["1", "да"], who="a", start=10)
    assert first_slot in r[0].text and r[1].won
    async with env4.sf() as s:
        lead = await s.scalar(select(Lead).where(Lead.external_id == "a"))
        booked = lead.extra["booking_at"]
    assert env4.agent.http.posts[-1][1]["title"] == "Konsultatsiya: Анна"
    # the next client never gets the same slot
    r = await talk(env4.agent, ["Здравствуйте, нужна консультация", "Борис", "+998935550000"], who="b")
    assert first_slot not in r[2].text
    # reminder an hour before
    async with env4.sf() as s:
        lead = await s.scalar(select(Lead).where(Lead.external_id == "a"))
        lead.extra = {**lead.extra, "booking_at": (utcnow() + timedelta(minutes=40)).isoformat()}
        await s.commit()
    assert await env4.agent.booking_reminders() == 1
    assert "Напоминание: ваша консультация сегодня" in env4.sink.sent[-1][2]
    assert await env4.agent.booking_reminders() == 0
    assert datetime.fromisoformat(booked) > utcnow()


async def test_routing_by_service_and_language(env4):
    await talk(env4.agent, ["Istanbul turi narxi qancha?", "operator kerak"], who="d")
    await talk(env4.agent, ["Merhaba, pasaport sorgulama fiyatı ne kadar?", "operatör istiyorum, yetkili ile konuşmak"], who="t")
    await talk(env4.agent, ["Salom", "operator"], who="u")
    by = {}
    for tid, text in env4.notifier.sent:
        by.setdefault(tid, []).append(text)
    assert any("Istanbul" in t or "Истанбул" in t for t in by.get(222, []))
    assert 111 in by and 333 in by and DIRECTOR not in by


async def test_sla_escalates_to_directors_once(env4):
    await talk(env4.agent, ["Salom", "operator bilan gaplashmoqchiman"], who="s")
    async with env4.sf() as s:
        lead = await s.scalar(select(Lead))
        lead.extra = {**lead.extra, "waiting_since": (utcnow() - timedelta(minutes=20)).isoformat(timespec="seconds")}
        await s.commit()
    assert await env4.agent.sla_check() == 1
    assert any(tid == DIRECTOR and "15 дақиқадан бери жавоб йўқ" in t for tid, t in env4.notifier.sent)
    assert await env4.agent.sla_check() == 0
    ok, _ = await env4.agent.manager_reply(1, "Hozir qo'ng'iroq qilaman")
    async with env4.sf() as s:
        assert "waiting_since" not in (await s.get(Lead, 1)).extra
