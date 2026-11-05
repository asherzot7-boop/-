"""Sotuvni oshirish: pasport natijasidan keyingi taklif, bilimlar bazasi, Instagram izohlari."""
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import create_api
from app.notify import ConsoleNotifier
from app.salesbot.agent import SalesAgent
from app.salesbot.channels import meta
from app.salesbot.models import FaqEntry, Lead, LeadStage
from app.salesbot.outbound import Outbound
from tests.conftest import DIRECTOR, build_env, make_settings

MANAGER = 777
ROOT = Path(__file__).resolve().parents[1]


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, channel, recipient, text, extra=None):
        self.sent.append((channel, recipient, text))
        return True


@pytest.fixture
async def env2(tmp_path):
    settings = make_settings(tmp_path, sales_catalog_path=str(ROOT / "sales_catalog.json"), sales_faq_path=str(ROOT / "sales_faq.json"),
                             sales_catalog_from_books=False, sales_manager_ids=[MANAGER], sales_shop_name="Jihan Jan Jasur Nam Tour",
                             sales_files_dir=str(tmp_path / "files"), sales_payment_card="8600 0000 0000 0000", sales_usd_rate=12800,
                             meta_app_secret="appsecret", instagram_access_token="ig-token")
    e = await build_env(settings)
    e.notifier = ConsoleNotifier()
    e.sender = FakeSender()
    out = Outbound()
    out.register(("instagram", "whatsapp", "telegram"), e.sender)
    e.agent = SalesAgent(e.sf, settings, notifier=e.notifier, outbound=out)
    e.service.sales = e.agent
    yield e
    await e.engine.dispose()


async def talk(agent, lines, who="u1", channel="instagram", start=0):
    return [await agent.handle(channel, who, line, message_id=f"{who}-{start + i}") for i, line in enumerate(lines)]


PASSPORT = ["Pasportni tekshirish kerak", "1", "Ismim Jasur", "+998 90 777 66 55", "Ertaga kerak, O'zbekiston pasporti", "ha"]


# ------------------------------------------------------------------ 1. upsell after the passport check
async def test_ban_result_offers_deport_and_continues_to_application(env2):
    r = await talk(env2.agent, PASSPORT)
    assert r[-1].won
    reply = await env2.service.dispatch(DIRECTOR, "/result 1 ban 2023-yildan, 5 yilga")
    assert "Натижа юборилди" in reply.text and "янги лид #2" in reply.text
    _, to, text = env2.sender.sent[-1]
    assert to == "u1"
    assert "taqiq (deport) bor. 2023-yildan, 5 yilga" in text
    assert "Deportni olib tashlash" in text and "2 600 $" in text and "Oldindan to'lov: 600 $" in text and "«Ha»" in text
    r = await talk(env2.agent, ["ha", "2023-yil iyun, 5 yilga", "ha"], start=10)
    assert "Deport qachon va necha yilga" in r[0].text  # name and phone are already known
    assert "Bekzod" not in r[1].text and "Jasur, +998907776655" in r[1].text
    assert r[2].won and "Oldindan to'lov: 600 $" in r[2].text
    async with env2.sf() as s:
        deport = await s.get(Lead, 2)
        assert (deport.product_id, deport.stage, deport.extra["from_lead"]) == ("deport", LeadStage.WON, 1)


async def test_ok_result_offers_work_documents_and_no_is_respected(env2):
    await talk(env2.agent, PASSPORT)
    await env2.service.dispatch(DIRECTOR, "/result 1 ok")
    text = env2.sender.sent[-1][2]
    assert "taqiq yo'q ✅" in text and "Turkiyada ishlash uchun hujjatlar" in text and "800 $" in text
    r = await talk(env2.agent, ["yo'q, rahmat"], start=20)
    assert "Mayli!" in r[0].text
    async with env2.sf() as s:
        assert (await s.get(Lead, 2)).stage == LeadStage.LOST


async def test_result_command_validates_input(env2):
    await talk(env2.agent, PASSPORT)
    assert "ban" in (await env2.service.dispatch(DIRECTOR, "/result 1 maybe")).text
    assert "Фойдаланиш" in (await env2.service.dispatch(DIRECTOR, "/result 1")).text


# ------------------------------------------------------------------ 2. knowledge base
async def test_curated_faq_answers_in_the_clients_language(env2):
    r = await talk(env2.agent, ["Pasport tekshiruvi natijasi qancha vaqtda chiqadi?"], who="q1")
    assert r[0].text.startswith("Pasport tekshiruvi (sorgulama) natijasi 12 soat ichida chiqadi.")
    r = await talk(env2.agent, ["Merhaba, pasaport sorgulama sonucu ne zaman çıkar?"], who="q2")
    assert r[0].text.startswith("Pasaport sorgulama sonucu 12 saat içinde çıkar.")
    r = await talk(env2.agent, ["Kafolat berasizmi?"], who="q3")
    assert "100% kafolat bera olmaydi" in r[0].text


async def test_unknown_question_goes_to_manager_and_is_learned(env2):
    r = await talk(env2.agent, ["Assalomu alaykum", "Ofisingiz qayerda joylashgan?"], who="q4")
    assert r[1].text.startswith("Bu savolga konsultantimiz aniq javob beradi")
    card = env2.notifier.sent[-1][1]
    assert "❓ Савол" in card and "/answer" in card and "Ofisingiz qayerda" in card
    reply = await env2.service.dispatch(DIRECTOR, "/answer 1 Toshkent, Chilonzor 5-kvartal, 12-uy. Ish vaqti 9:00–18:00")
    assert "базага қўшилди" in reply.text
    assert env2.sender.sent[-1][2] == "👤 Menejer: Toshkent, Chilonzor 5-kvartal, 12-uy. Ish vaqti 9:00–18:00"
    r = await talk(env2.agent, ["Salom, ofisingiz qayerda?"], who="q5")
    assert r[0].text.startswith("Toshkent, Chilonzor 5-kvartal")
    async with env2.sf() as s:
        assert (await s.scalar(select(FaqEntry))).uses == 1
    assert "Ofisingiz qayerda" in (await env2.service.dispatch(DIRECTOR, "/faq")).text
    await env2.service.dispatch(DIRECTOR, "/faq del 1")
    r = await talk(env2.agent, ["Ofisingiz qayerda joylashgan?"], who="q6")
    assert r[0].text.startswith("Bu savolga konsultantimiz")


async def test_price_questions_are_not_sent_to_the_manager(env2):
    r = await talk(env2.agent, ["Deport narxi qancha?"], who="q7")
    assert "2 600 $" in r[0].text and "konsultantimiz aniq javob" not in r[0].text


# ------------------------------------------------------------------ 3. Instagram comments
class FakeMeta:
    def __init__(self, dm_ok=True):
        self.private, self.public, self.dm_ok = [], [], dm_ok

    async def private_reply(self, comment_id, text):
        self.private.append((comment_id, text))
        return self.dm_ok

    async def comment_reply(self, comment_id, text):
        self.public.append((comment_id, text))
        return True

    async def send(self, channel, recipient, text):
        return True


def _comment(cid, text, sender="777001", own="17841400000"):
    body = json.dumps({"object": "instagram", "entry": [{"id": own, "changes": [{"field": "comments", "value": {
        "id": cid, "text": text, "from": {"id": sender, "username": "mijoz_uz"}, "media": {"id": "post1"}}}]}]}).encode()
    return body, {"X-Hub-Signature-256": "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest(),
                  "Content-Type": "application/json"}


async def test_comment_gets_a_private_dm_and_a_short_public_reply(env2):
    fake = FakeMeta()
    client = TestClient(create_api(env2.settings, env2.sf, sales=env2.agent, meta_sender=fake))
    body, headers = _comment("c1", "Deport narxi qancha?")
    client.post("/webhooks/meta", content=body, headers=headers)
    assert fake.private and fake.private[0][0] == "c1" and "2 600 $" in fake.private[0][1]
    assert fake.public[0][0] == "c1" and "Direct" in fake.public[0][1]
    client.post("/webhooks/meta", content=body, headers=headers)  # webhook retry
    assert len(fake.private) == 1
    body, headers = _comment("c2", "Zo'r 👍🔥", sender="777002")
    client.post("/webhooks/meta", content=body, headers=headers)
    assert len(fake.private) == 1  # praise is not a question: no DM in intent mode
    body, headers = _comment("c3", "Narxi qancha?", sender="17841400000")
    client.post("/webhooks/meta", content=body, headers=headers)
    assert len(fake.private) == 1  # our own account


async def test_comment_falls_back_to_public_invite_when_dm_fails(env2):
    fake = FakeMeta(dm_ok=False)
    client = TestClient(create_api(env2.settings, env2.sf, sales=env2.agent, meta_sender=fake))
    body, headers = _comment("c9", "Merhaba, pasaport sorgulama fiyatı ne kadar?", sender="777009")
    client.post("/webhooks/meta", content=body, headers=headers)
    assert fake.public[0][1] == "Lütfen DM'den yazın — fiyat ve koşulları iletelim ✅"


def test_parse_ignores_other_instagram_changes():
    items = meta.parse({"object": "instagram", "entry": [{"id": "1", "changes": [{"field": "mentions", "value": {"media_id": "x"}}]}]})
    assert items == []
