"""Сотув агенти: тил, воронка, буюртма, менежерга хабар, Meta ва Twilio webhook'лари."""
import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import create_api
from app.database.models import Order
from app.notify import ConsoleNotifier
from app.salesbot import lang as L
from app.salesbot.agent import SalesAgent, clean_phone
from app.salesbot.channels import meta
from app.salesbot.models import Lead, LeadMessage, LeadStage
from app.services import books
from tests.conftest import DIRECTOR, build_env, make_settings

MANAGER = 777


@pytest.fixture
async def sales(tmp_path):
    settings = make_settings(tmp_path, sales_catalog_path=None, sales_catalog_from_books=True, sales_manager_ids=[MANAGER], meta_app_secret="appsecret",
                             meta_verify_token="verify-me", twilio_auth_token="twiliotoken", sales_public_base_url="https://shop.example")
    e = await build_env(settings)
    async with e.sf() as s:
        b = await books.create_book(s, "Namangan ertaklari", kind="book", price=60000)
        await books.create_book(s, "Ona tili 5-sinf", kind="textbook", price=45000)
        await s.commit()
    e.book_id = b.id
    e.notifier = ConsoleNotifier()
    e.agent = SalesAgent(e.sf, settings, notifier=e.notifier)
    yield e
    await e.engine.dispose()


async def talk(agent, lines, channel="instagram", who="u1", **kw):
    out = []
    for i, line in enumerate(lines):
        out.append(await agent.handle(channel, who, line, message_id=f"{who}-{i}", **kw))
    return out


def test_language_detection():
    assert L.detect("Salom, narxi qancha?")[0] == "uz"
    assert L.detect("Ассалому алайкум, нархи қанча?")[0] == "uzc"
    assert L.detect("Merhaba, fiyatı ne kadar?")[0] == "tr"
    assert L.detect("Здравствуйте, сколько стоит?")[0] == "ru"
    assert L.to_cyrillic("Yetkazib berish manzili, o'zingiz") == "Етказиб бериш манзили, ўзингиз"


def test_phone_normalisation():
    assert clean_phone("90 123 45 67") == "+998901234567"
    assert clean_phone("+90 532 123 45 67") == "+905321234567"
    assert clean_phone("0532 123 45 67") == "+905321234567"
    assert clean_phone("12345") is None


async def test_uzbek_funnel_creates_order_and_notifies_manager(sales):
    r = await talk(sales.agent, ["Assalomu alaykum", "Namangan ertaklari qancha?", "3 ta", "Aziza", "90 123 45 67",
                                 "Toshkent, Chilonzor 5-kvartal 12-uy", "ha"])
    assert "60 000" in r[1].text  # price comes from the catalog
    assert r[4].text.startswith("Yetkazib")
    assert "× 3 = 180 000" in r[5].text  # house numbers in the address do not change the quantity
    assert r[6].done and "qabul qilindi" in r[6].text
    async with sales.sf() as s:
        lead = await s.scalar(select(Lead))
        assert lead.stage == LeadStage.WON and lead.phone == "+998901234567"
        order = await s.get(Order, lead.order_id)
        assert order.book_id == sales.book_id and order.quantity == 3 and order.unit_price == 60000
    assert any(tid == MANAGER and "Янги буюртма" in text and "+998901234567" in text for tid, text in sales.notifier.sent)


async def test_turkish_pickup_and_cyrillic_uzbek(sales):
    r = await talk(sales.agent, ["Merhaba, Namangan ertaklari fiyatı ne kadar?", "2 tane istiyorum", "Ayşe", "+90 532 123 45 67", "gelip alırım", "evet"], who="tr")
    assert "Kaç adet" in r[0].text and "Kendisi alacak" in r[4].text and r[5].done
    r = await talk(sales.agent, ["Ассалому алайкум, она тили 5-синф дарслиги борми", "10 та", "Мен Бобур"], who="cy")
    assert "Она тили 5-синф — 45 000 сўм" in r[0].text
    assert r[2].text.startswith("Раҳмат, Бобур!")


async def test_everything_in_one_message(sales):
    r = await talk(sales.agent, ["Namangan ertaklari 5 ta, ismim Ali, +998 93 555 44 33"])
    assert r[0].text.startswith("Yetkazib")
    async with sales.sf() as s:
        lead = await s.scalar(select(Lead))
        assert (lead.quantity, lead.name, lead.phone) == (5, "Ali", "+998935554433")


async def test_operator_handoff_then_bot_stays_silent(sales):
    r = await talk(sales.agent, ["Здравствуйте, сказки есть?", "можно оператора", "алло?"], who="ru")
    assert r[1].done and "менеджер" in r[1].text
    assert r[2].text is None  # a manager has taken over
    assert any("алло?" in text for _, text in sales.notifier.sent)


async def test_discount_is_never_promised(sales):
    r = await talk(sales.agent, ["Namangan ertaklari", "chegirma bormi? qimmat"])
    assert "menejer" in r[1].text.lower()
    assert any("чегирма" in text for _, text in sales.notifier.sent)


async def test_duplicate_webhook_is_ignored(sales):
    a = await sales.agent.handle("whatsapp", "998901", "Salom", message_id="wamid.1")
    b = await sales.agent.handle("whatsapp", "998901", "Salom", message_id="wamid.1")
    assert a.text and b.text is None
    async with sales.sf() as s:
        assert len((await s.scalars(select(LeadMessage).where(LeadMessage.direction == "in"))).all()) == 1


class EvilModel:
    """AI that tries to set a fake product, a negative quantity and confirm an incomplete order."""

    async def analyse(self, **kw):
        return {"reply": "Hammasi bepul!", "product_id": "nope", "quantity": -5, "confirm": True, "phone": "abc"}


class BrokenModel:
    async def analyse(self, **kw):
        raise TimeoutError


async def test_ai_output_is_validated_and_failures_fall_back(sales):
    sales.agent.model = EvilModel()
    r = await talk(sales.agent, ["Namangan ertaklari"], who="ai")
    async with sales.sf() as s:
        lead = await s.scalar(select(Lead).where(Lead.external_id == "ai"))
        assert lead.quantity is None and lead.phone is None and lead.stage == LeadStage.QUANTITY
    assert "60 000" in r[0].text  # the first offer is always built from the catalog
    sales.agent.model = BrokenModel()
    r = await talk(sales.agent, ["Salom"], who="ai2")
    assert "Assalomu alaykum" in r[0].text


async def test_leads_command(sales):
    await talk(sales.agent, ["Namangan ertaklari 2 ta"])
    reply = await sales.service.dispatch(DIRECTOR, "/leads")
    assert "Namangan ertaklari × 2" in reply.text
    reply = await sales.service.dispatch(DIRECTOR, "/lead 1")
    assert "👤 Namangan ertaklari 2 ta" in reply.text


# ------------------------------------------------------------------ webhooks
class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, channel, recipient, text):
        self.sent.append((channel, recipient, text))
        return True


def _meta_sig(body: bytes) -> str:
    return "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()


async def test_meta_webhook(sales):
    sender = FakeSender()
    client = TestClient(create_api(sales.settings, sales.sf, sales=sales.agent, meta_sender=sender))
    ok = client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"}).status_code == 403

    ig = json.dumps({"object": "instagram", "entry": [{"messaging": [
        {"sender": {"id": "ig-1"}, "message": {"mid": "m1", "text": "Namangan ertaklari narxi?"}},
        {"sender": {"id": "page"}, "message": {"mid": "m2", "text": "echo", "is_echo": True}},
    ]}]}).encode()
    assert client.post("/webhooks/meta", content=ig, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 403
    resp = client.post("/webhooks/meta", content=ig, headers={"X-Hub-Signature-256": _meta_sig(ig), "Content-Type": "application/json"})
    assert resp.json() == {"received": 1}
    assert sender.sent[0][:2] == ("instagram", "ig-1") and "60 000" in sender.sent[0][2]

    wa = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "998901234567", "profile": {"name": "Dilshod"}}],
        "messages": [{"from": "998901234567", "id": "wamid.X", "type": "text", "text": {"body": "Salom, ertak kitob kerak, 2 ta"}}],
    }}]}]}).encode()
    client.post("/webhooks/meta", content=wa, headers={"X-Hub-Signature-256": _meta_sig(wa), "Content-Type": "application/json"})
    channel, to, text = sender.sent[-1]
    assert (channel, to) == ("whatsapp", "998901234567")
    # WhatsApp gives the name and number: the agent goes straight to the address
    assert text.startswith("Yetkazib")


def test_meta_parse_ignores_non_text():
    items = meta.parse({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]})
    assert items == []


def _twilio_sig(url: str, params: dict) -> str:
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(b"twiliotoken", data.encode(), hashlib.sha1).digest()).decode()


async def test_twilio_voice_call(sales):
    client = TestClient(create_api(sales.settings, sales.sf, sales=sales.agent))
    start = {"CallSid": "CA1", "From": "+998901112233"}
    assert client.post("/webhooks/twilio/voice", data=start, headers={"X-Twilio-Signature": "bad"}).status_code == 403
    r = client.post("/webhooks/twilio/voice", data=start, headers={"X-Twilio-Signature": _twilio_sig("https://shop.example/webhooks/twilio/voice", start)})
    assert r.status_code == 200 and "<Gather" in r.text and 'language="uz-UZ"' in r.text and "Assalomu alaykum" in r.text

    url = "https://shop.example/webhooks/twilio/voice/gather"
    said = []
    for phrase in ["Namangan ertaklari kerak", "ikkita", "Ismim Jasur", "Olib ketaman", "Ha"]:
        p = {"CallSid": "CA1", "From": "+998901112233", "SpeechResult": phrase}
        said.append(client.post("/webhooks/twilio/voice/gather", data=p, headers={"X-Twilio-Signature": _twilio_sig(url, p)}).text)
    assert "60 000" in said[0]
    assert "Buyurtmangizni tekshiring" in said[3] and "+998901112233" in said[3]  # caller ID is the phone, never asked
    assert "<Hangup/>" in said[4] and "qabul qilindi" in said[4]
    assert "✅" not in said[4]  # emojis are not read aloud


# ------------------------------------------------------------------ services: visa, tours, consulting
@pytest.fixture
async def services(tmp_path):
    from pathlib import Path

    catalog = Path(__file__).resolve().parents[1] / "sales_catalog.example.json"
    settings = make_settings(tmp_path, sales_catalog_path=str(catalog), sales_catalog_from_books=False, sales_manager_ids=[MANAGER],
                             sales_shop_name="Visa & Travel")
    e = await build_env(settings)
    e.notifier = ConsoleNotifier()
    e.agent = SalesAgent(e.sf, settings, notifier=e.notifier)
    yield e
    await e.engine.dispose()


async def test_visa_service_with_documents(services):
    r = await talk(services.agent, ["Assalomu alaykum", "Turkiyada ikamet qilish kerak, qanday hujjatlar kerak?", "2 kishi", "Ismim Sardor",
                                    "+998 90 111 22 33", "Istanbul, yanvardan", "ha"])
    assert "150 $ dan" in r[0].text and "200 000 so'm" in r[0].text
    assert "Ijara shartnomasi" in r[1].text and "Necha kishi" in r[1].text
    assert "Arizani" in r[2].text
    assert "qaysi shahrida" in r[4].text  # the service's own question replaces "delivery address"
    assert "2 kishi — 300 $ dan" in r[5].text
    assert r[6].done and "Arizangiz" in r[6].text
    card = services.notifier.sent[-1][1]
    assert "Янги ариза" in card and "Сана / шаҳар: Istanbul, yanvardan" in card


async def test_turkish_tour_names_are_in_turkish(services):
    r = await talk(services.agent, ["Merhaba, Semerkant Buhara turu var mı?", "4 kişi", "Mehmet", "0532 111 22 33", "Nisan ayında", "evet"], who="tr")
    assert r[0].text.startswith("Özbekistan turu: Taşkent") and "690 $ ve üzeri" in r[0].text and "Kaç kişi" in r[0].text
    assert "4 kişi — 2 760 $ ve üzeri" in r[4].text
    assert r[5].done and "başvurunuz alındı" in r[5].text


async def test_consultation_skips_quantity(services):
    r = await talk(services.agent, ["Здравствуйте, нужна консультация по визе", "Анна"], who="ru")
    assert "200 000 сум" in r[0].text and "как вас зовут" in r[0].text
    assert "телефон" in r[1].text
