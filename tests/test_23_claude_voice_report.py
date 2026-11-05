"""Claude modeli, pasport MRZ, ovozli javob va haftalik hisobot."""
from datetime import timedelta
from pathlib import Path

import pytest

from app.database.models import utcnow
from app.notify import ConsoleNotifier
from app.salesbot import passport
from app.salesbot.agent import AI_SCHEMA, AnthropicSalesModel, SalesAgent, build_sales_model
from app.salesbot.models import Lead
from app.salesbot.tts import AzureTTS, latin_uz
from tests.conftest import DIRECTOR, build_env, make_settings

ROOT = Path(__file__).resolve().parents[1]
MRZ = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"   # ICAO 9303 specimen


def test_mrz_check_digits():
    info = passport.parse_mrz("some text\n" + MRZ)
    assert info["surname"] == "Eriksson" and info["number"] == "L898902C3" and info["expiry"] == "2012-04-15"
    assert passport.parse_mrz(MRZ.replace("7408122", "7408123")) is None      # one misread digit -> rejected


def test_claude_is_preferred_when_its_key_is_set(tmp_path):
    s = make_settings(tmp_path, anthropic_api_key="sk-ant-test", openai_api_key="sk-test")
    model = build_sales_model(s)
    assert isinstance(model, AnthropicSalesModel) and model.model == "claude-haiku-5-5"
    assert set(AI_SCHEMA["required"]) == set(AI_SCHEMA["properties"]) and AI_SCHEMA["additionalProperties"] is False


async def test_claude_request_shape(tmp_path):
    sent = {}

    class Msgs:
        async def create(self, **kw):
            sent.update(kw)

            class B:
                type, text = "text", '{"reply":"Salom","product_id":"","quantity":0,"name":"","phone":"","address":"","pickup":false,"confirm":false,"decline":false,"operator":false,"discount":false}'

            class R:
                stop_reason, content = "end_turn", [B()]
            return R()

    m = AnthropicSalesModel("sk-ant-test")
    m._client = type("C", (), {"messages": Msgs()})()
    out = await m.analyse(language="uz", shop="X", products=[], lead={}, message="Salom",
                          history=[{"role": "assistant", "content": "greet"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
    assert out["reply"] == "Salom"
    assert sent["model"] == "claude-haiku-5-5" and sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["type"] == "json_schema" and "temperature" not in sent
    roles = [x["role"] for x in sent["messages"]]
    assert roles[0] == "user" and all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))


async def test_azure_tts_request_and_cyrillic_to_latin():
    calls = []

    class Http:
        async def post(self, url, content, headers):
            calls.append((url, content.decode(), headers))

            class R:
                status_code, content = 200, b"OggS"
            return R()

    tts = AzureTTS("key", "westeurope", client=Http())
    assert await tts.synth("Ассалому алайкум! 😊 Нархи — 80 $", "uzc") == b"OggS"
    url, ssml, headers = calls[0]
    assert url == "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1"
    assert "uz-UZ-MadinaNeural" in ssml and "Assalomu alaykum" in ssml and "😊" not in ssml
    assert headers["X-Microsoft-OutputFormat"] == "ogg-24khz-16bit-mono-opus"
    assert latin_uz("Тошкент, Ўзбекистон") == "Toshkent, O'zbekiston"


class FakeTTS:
    async def synth(self, text, lang, fmt="ogg"):
        return b"OggS" + text[:5].encode()


class FakeTranscriber:
    enabled = True

    async def transcribe(self, data, name):
        return "Deport narxi qancha?"


@pytest.fixture
async def rep(tmp_path):
    settings = make_settings(tmp_path, sales_catalog_path=str(ROOT / "sales_catalog.json"), sales_faq_path=None, sales_catalog_from_books=False,
                             sales_consent_required=False, sales_files_dir=str(tmp_path / "f"))
    e = await build_env(settings)
    e.agent = SalesAgent(e.sf, settings, notifier=ConsoleNotifier(), transcriber=FakeTranscriber(), tts=FakeTTS())
    e.service.sales = e.agent
    yield e
    await e.engine.dispose()


async def test_voice_note_is_answered_with_voice(rep):
    r = await rep.agent.handle_file("telegram", "v", kind="audio", data=b"OggS", message_id="v1")
    assert "2 600 $" in r.text and r.voice.startswith(b"OggS")


async def test_weekly_report_compares_weeks_and_suggests(rep):
    a = rep.agent
    for i, x in enumerate(["Deportim bor 3 yillik, narxi qancha?", "Ismim Bek", "+998901112233", "2023, 3 yil", "ha"]):
        await a.handle("telegram", "w1", x, message_id=f"a{i}")
    for who in ("w2", "w3", "w4"):
        await a.handle("telegram", who, "Deport narxi qancha?", message_id=who)
    await a.handle("telegram", "w5", "Ofisingiz qayerda?", message_id="q1")
    async with rep.sf() as s:
        old = Lead(channel="telegram", external_id="old", language="uz")
        s.add(old)
        await s.flush()
        old.created_at = utcnow() - timedelta(days=10)
        await s.commit()
    text = await a.weekly_report()
    assert "Мижозлар: 5 (ўтган ҳафта 1, +400%)" in text and "Аризалар: 1" in text and "конверсия 20%" in text
    assert "нарх айтилгандан кейин (исм) (3 та)" in text and "Кўп мижоз нархни билгач тўхтаяпти" in text
    assert "1 та ариза олдиндан тўловни кутмоқда" in text
    assert "Ҳафталик ҳисобот" in (await rep.service.dispatch(DIRECTOR, "/weekly")).text


async def test_weekly_report_is_due_once_a_week(rep):
    from datetime import datetime

    monday_10 = datetime(2026, 11, 9, 5, 0)          # 10:00 in Tashkent
    assert await rep.agent.weekly_report_due(monday_10) is True
    assert await rep.agent.weekly_report_due(monday_10 + timedelta(hours=1)) is False
    assert await rep.agent.weekly_report_due(datetime(2026, 11, 10, 5, 0)) is False   # Tuesday
