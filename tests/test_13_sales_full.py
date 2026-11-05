"""Сотув агентининг тўлиқ версияси: файллар, овоз, тўлов, менежер жавоби, эслатма, жадвал."""
import base64
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import create_api
from app.database.models import utcnow
from app.notify import ConsoleNotifier
from app.salesbot import payments
from app.salesbot.agent import SalesAgent
from app.salesbot.channels import meta
from app.salesbot.models import Lead, LeadFile
from app.salesbot.outbound import Outbound
from tests.conftest import DIRECTOR, build_env, make_settings

MANAGER = 777
CATALOG = Path(__file__).resolve().parents[1] / "sales_catalog.json"


class FakeSender:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    async def send(self, channel, recipient, text, extra=None):
        self.sent.append((channel, recipient, text, extra))
        return self.ok


class FakeTranscriber:
    enabled = True

    def __init__(self, text):
        self.text = text

    async def transcribe(self, data, name):
        return self.text


@pytest.fixture
async def full(tmp_path):
    settings = make_settings(tmp_path, sales_catalog_path=str(CATALOG), sales_catalog_from_books=False, sales_manager_ids=[MANAGER],
                             sales_shop_name="Jihan Jan Jasur Nam Tour", sales_files_dir=str(tmp_path / "files"),
                             sales_payment_provider="card", sales_payment_card="8600 1234 5678 9012 (Jasur N.)", sales_usd_rate=12800,
                             api_token="secret-token")
    e = await build_env(settings)
    e.notifier = ConsoleNotifier()
    e.sender = FakeSender()
    out = Outbound()
    out.register(("instagram", "whatsapp", "messenger", "telegram", "telegram_business"), e.sender)
    e.agent = SalesAgent(e.sf, settings, notifier=e.notifier, outbound=out)
    e.service.sales = e.agent
    yield e
    await e.engine.dispose()


async def talk(agent, lines, channel="instagram", who="u1"):
    return [await agent.handle(channel, who, line, message_id=f"{who}-{i}") for i, line in enumerate(lines)]


DEPORT = ["Deportim bor 3 yillik, narxi qancha?", "Ismim Bekzod", "+998 91 222 33 44", "2025-yil mart, 3 yilga", "ha"]


async def test_prepayment_instructions_after_confirmation(full):
    r = await talk(full.agent, DEPORT)
    last = r[-1]
    assert last.won and "Arizangiz №1" in last.text
    assert "Oldindan to'lov: 600 $ (≈ 7 680 000 so'm)" in last.text and "8600 1234 5678 9012" in last.text and "№1" in last.text
    async with full.sf() as s:
        lead = await s.get(Lead, 1)
        assert (lead.prepayment_due, lead.currency) == (600, "USD")
    assert "/paid 1" in full.notifier.sent[-1][1]


def test_payme_and_click_links():
    class S:
        sales_payment_provider = "payme"; payme_merchant_id = "MID"; payme_account_field = "order_id"; sales_usd_rate = 12800
        click_service_id = "11"; click_merchant_id = "22"; sales_payment_link_template = None; sales_payment_card = None

    link = payments.payment_link(S, 5, 600, "USD")
    assert base64.b64decode(link.rsplit("/", 1)[1]).decode() == "m=MID;ac.order_id=5;a=768000000"  # tiyin
    S.sales_payment_provider = "click"
    assert payments.payment_link(S, 5, 600, "USD").endswith("service_id=11&merchant_id=22&amount=7680000&transaction_param=5")
    S.sales_usd_rate = None
    assert payments.payment_link(S, 5, 600, "USD") is None  # no rate -> no so'm amount -> no link


async def test_paid_command_notifies_client(full):
    await talk(full.agent, DEPORT)
    reply = await full.service.dispatch(DIRECTOR, "/paid 1")
    assert "тўлов белгиланди" in reply.text
    assert full.sender.sent[-1][:2] == ("instagram", "u1") and "To'lovingiz qabul qilindi" in full.sender.sent[-1][2]
    again = await full.service.dispatch(DIRECTOR, "/paid 1")
    assert "аллақачон" in again.text


async def test_manager_reply_goes_to_the_clients_channel(full):
    await talk(full.agent, ["Pasport tekshirish kerak"], channel="whatsapp", who="998901112233")
    reply = await full.service.dispatch(DIRECTOR, "/reply 1 Pasportingiz tekshirildi, taqiq yo'q")
    assert "Юборилди" in reply.text
    assert full.sender.sent[-1][:3] == ("whatsapp", "998901112233", "👤 Menejer: Pasportingiz tekshirildi, taqiq yo'q")
    await talk(full.agent, ["Salom"], channel="phone", who="+998901234567")
    reply = await full.service.dispatch(DIRECTOR, "/reply 2 salom")
    assert "қўнғироқ қилинг" in reply.text


async def test_document_is_saved_and_forwarded(full):
    pdf = b"%PDF-1.4 fake"
    r = await full.agent.handle_file("telegram", "42", kind="document", data=pdf, file_name="../../pasport.pdf", mime="application/pdf",
                                     message_id="42:7", display_name="Aziz Karimov")
    assert "qabul qilindi" in r.text and "1 ta" in r.text
    async with full.sf() as s:
        f = await s.scalar(select(LeadFile))
        assert f.file_name == "pasport.pdf" and Path(f.path).read_bytes() == pdf
        assert Path(f.path).resolve().is_relative_to(Path(full.settings.sales_files_dir).resolve())  # no path traversal
    assert any("[файл:" in text and "pasport.pdf" in text for tid, text in full.notifier.sent if tid == MANAGER)
    dup = await full.agent.handle_file("telegram", "42", kind="document", data=pdf, file_name="pasport.pdf", message_id="42:7")
    assert dup.text is None
    listing = await full.service.dispatch(DIRECTOR, "/files 1")
    assert "pasport.pdf" in listing.text


async def test_too_big_and_failed_downloads(full):
    r = await full.agent.handle_file("telegram", "43", kind="document", data=None, too_big=True, file_name="big.pdf", message_id="43:1")
    assert "juda katta" in r.text
    r = await full.agent.handle_file("telegram", "43", kind="photo", data=None, message_id="43:2")
    assert "ololmadim" in r.text


async def test_voice_message_is_transcribed_and_answered(full):
    full.agent.transcriber = FakeTranscriber("Salom, deportni olib tashlash qancha turadi?")
    r = await full.agent.handle_file("telegram", "44", kind="audio", data=b"OggS", message_id="44:1")
    assert "2 600 $" in r.text
    async with full.sf() as s:
        f = await s.scalar(select(LeadFile))
        assert f.kind == "audio" and f.transcript.startswith("Salom, deportni")
    full.agent.transcriber = None
    r = await full.agent.handle_file("telegram", "45", kind="audio", data=b"OggS", message_id="45:1")
    assert "Ovozli xabarni tushuna olmadim" in r.text


async def test_reminder_once_and_only_inside_the_24h_window(full):
    await talk(full.agent, ["Deport narxi qancha?"], who="a")
    await talk(full.agent, ["Pasport tekshirish kerak"], who="b")
    async with full.sf() as s:
        a = await s.scalar(select(Lead).where(Lead.external_id == "a"))
        b = await s.scalar(select(Lead).where(Lead.external_id == "b"))
        a.last_message_at = utcnow() - timedelta(hours=21)
        b.last_message_at = utcnow() - timedelta(hours=30)  # Instagram window closed
        await s.commit()
    assert await full.agent.remind_stale() == 1
    _, to, text, _ = full.sender.sent[-1]
    assert to == "a" and text.startswith("Assalomu alaykum! Arizangizni tugatamizmi?") and "ismingiz" in text
    assert await full.agent.remind_stale() == 0  # never twice


async def test_sheets_row_on_confirmation(full, monkeypatch):
    rows = []

    class FakeHttp:
        async def post(self, url, json):
            rows.append((url, json))

            class R:
                status_code = 200
            return R()

    full.settings.sales_sheets_webhook_url = __import__("pydantic").SecretStr("https://script.google.com/macros/s/X/exec")
    full.agent.http = FakeHttp()
    await talk(full.agent, DEPORT)
    assert rows and rows[-1][1]["event"] == "won" and rows[-1][1]["total"] == 2600 and rows[-1][1]["prepayment"] == 600
    await full.agent.mark_paid(1)
    assert rows[-1][1]["event"] == "paid" and rows[-1][1]["paid"] is True


def test_meta_parse_attachments():
    ig = meta.parse({"object": "instagram", "entry": [{"messaging": [
        {"sender": {"id": "ig-9"}, "message": {"mid": "m9", "attachments": [{"type": "image", "payload": {"url": "https://cdn.example/p.jpg"}}]}}]}]})
    assert ig[0].attachment == {"kind": "photo", "url": "https://cdn.example/p.jpg", "name": None, "mime": None} and ig[0].message_id == "m9:0"
    wa = meta.parse({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [
        {"from": "99890", "id": "wamid.D", "type": "document", "document": {"id": "MEDIA1", "filename": "vergi.pdf", "mime_type": "application/pdf", "caption": "vergi levhasi"}},
        {"from": "99890", "id": "wamid.V", "type": "audio", "audio": {"id": "MEDIA2", "mime_type": "audio/ogg; codecs=opus", "voice": True}},
    ]}}]}]})
    assert [(x.attachment["kind"], x.attachment["media_id"], x.text) for x in wa] == [("document", "MEDIA1", "vergi levhasi"), ("audio", "MEDIA2", "")]


async def test_leads_csv_export(full):
    await talk(full.agent, DEPORT)
    client = TestClient(create_api(full.settings, full.sf, sales=full.agent))
    assert client.get("/api/leads.csv").status_code == 401
    resp = client.get("/api/leads.csv", headers={"X-API-Token": "secret-token"})
    assert resp.status_code == 200 and "Bekzod" in resp.text and "won" in resp.text
