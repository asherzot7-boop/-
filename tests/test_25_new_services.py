"""Янги хизматлар: иш рухсатномаси, ikamet ва таржима — нархи келишилади, агент нарх ўйлаб топмайди."""
from pathlib import Path

import pytest

from app.notify import ConsoleNotifier
from app.salesbot.agent import SalesAgent
from app.salesbot.catalog import load_file, match
from tests.conftest import build_env, make_settings

CATALOG = Path(__file__).resolve().parents[1] / "sales_catalog.json"


@pytest.mark.parametrize(("text", "service"), [
    ("Ikamet uzaytirish kerak", "ikamet"), ("İkamet izni uzatma", "ikamet"), ("Нужно продлить ВНЖ", "ikamet"),
    ("Diplomimni tarjima qilish kerak", "tarjima"), ("Перевод документов и апостиль", "tarjima"),
    ("Çalışma izni almak istiyorum", "ish-ruxsatnoma"), ("Разрешение на работу в Турции", "ish-ruxsatnoma"),
    ("Turkiyada ishlash uchun hujjat kerak", "ish-beruvchi"), ("Deport narxi qancha", "deport"),
    ("Pasportimni tekshirish kerak", "pasport-sorgulama"),
    ("İşverenim var, hangi belgeler gerekli?", "ish-beruvchi"),
])
def test_new_services_do_not_steal_old_ones(text, service):
    found = match(load_file(CATALOG), text)
    assert (found.id if found else None) == service


@pytest.fixture
async def agent(tmp_path):
    settings = make_settings(tmp_path, sales_catalog_path=str(CATALOG), sales_catalog_from_books=False, sales_manager_ids=[777],
                             sales_files_dir=str(tmp_path / "files"), sales_consent_required=False)
    e = await build_env(settings)
    e.notifier = ConsoleNotifier()
    yield SalesAgent(e.sf, settings, notifier=e.notifier), e.notifier
    await e.engine.dispose()


async def test_ikamet_application_has_no_invented_price(agent):
    a, notifier = agent
    lines = ["Assalomu alaykum, ikametimni uzaytirish kerak, narxi qancha?", "1", "Ismim Malika", "+998 90 123 45 67",
             "Uzaytirish, mart oyida tugaydi", "ha"]
    replies = [await a.handle("instagram", "m1", t, message_id=f"m1-{i}") for i, t in enumerate(lines)]
    first = replies[0].text
    assert "narxi kelishiladi" in first and "konsultant" in first and "$" not in first
    assert replies[-1].won
    assert any("Yashash ruxsatnomasi" in text for _, text in notifier.sent)
    assert not any("shartnoma" in name for name in await a.build_documents(1))  # no contract while the price is not agreed


async def test_translation_in_turkish(agent):
    a, _ = agent
    r = await a.handle("whatsapp", "t1", "Merhaba, diploma tercümesi ve apostil lazım. Fiyatı ne kadar?", message_id="t1-0", phone="+905551112233")
    assert "Tercüme, noter onayı" in r.text and "fiyat görüşülür" in r.text
