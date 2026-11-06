"""Shaxsiy ma'lumotlar: rozilik, shifrlash, avtomatik o'chirish, kirish jurnali."""
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import create_api
from app.database.models import AuditLog, utcnow
from app.notify import ConsoleNotifier
from app.salesbot import media
from app.salesbot.agent import SalesAgent
from app.salesbot.models import Lead, LeadFile
from tests.conftest import build_env, make_settings

ROOT = Path(__file__).resolve().parents[1]
KEY = media.new_key()


@pytest.fixture
async def priv(tmp_path):
    settings = make_settings(tmp_path, api_token="t", sales_catalog_path=str(ROOT / "sales_catalog.json"), sales_catalog_from_books=False,
                             sales_manager_ids=[777], sales_files_dir=str(tmp_path / "files"), sales_files_key=KEY, sales_file_retention_days=30)
    e = await build_env(settings)
    e.notifier = ConsoleNotifier()
    e.agent = SalesAgent(e.sf, settings, notifier=e.notifier)
    yield e
    await e.engine.dispose()


async def test_no_document_is_stored_before_consent(priv):
    a = priv.agent
    r = await a.handle_file("telegram", "9", kind="document", data=b"%PDF secret", file_name="pasport.pdf", message_id="9:1")
    assert "Rozimisiz" in r.text and "30 kundan keyin" in r.text
    async with priv.sf() as s:
        assert (await s.scalars(select(LeadFile))).all() == []
    assert not any("[файл:" in t for _, t in priv.notifier.sent)
    r = await a.handle("telegram", "9", "Roziman", message_id="9:2")
    assert r.text.startswith("Rahmat! Endi hujjatni qayta yuboring")
    r = await a.handle_file("telegram", "9", kind="document", data=b"%PDF secret", file_name="pasport.pdf", message_id="9:3")
    assert "qabul qilindi" in r.text


async def test_files_are_encrypted_on_disk_and_readable_in_the_panel(priv):
    a = priv.agent
    await a.handle("telegram", "9", "Roziman", message_id="x")  # no pending request: just a message
    async with priv.sf() as s:
        lead = await s.scalar(select(Lead))
        lead.extra = {"consent": "2026-11-05T10:00:00"}
        await s.commit()
    await a.handle_file("telegram", "9", kind="document", data=b"%PDF secret", file_name="pasport.pdf", mime="application/pdf", message_id="9:4")
    async with priv.sf() as s:
        f = await s.scalar(select(LeadFile))
    raw = Path(f.path).read_bytes()
    assert f.path.endswith(".enc") and b"secret" not in raw
    assert media.read(f.path, KEY) == b"%PDF secret"
    client = TestClient(create_api(priv.settings, priv.sf, sales=a))
    resp = client.get(f"/api/panel/files/{f.id}", headers={"X-API-Token": "t"})
    assert resp.status_code == 200 and resp.content == b"%PDF secret"
    async with priv.sf() as s:
        actions = [x.action for x in await s.scalars(select(AuditLog))]
    assert "file.forwarded" in actions and "file.viewed" in actions


async def test_old_files_are_purged(priv):
    a = priv.agent
    async with priv.sf() as s:
        s.add(Lead(channel="telegram", external_id="7", language="uz", extra={"consent": "x"}))
        await s.commit()
    await a.handle_file("telegram", "7", kind="document", data=b"old", file_name="eski.pdf", message_id="7:1")
    async with priv.sf() as s:
        lead = await s.scalar(select(Lead))
        lead.last_message_at = utcnow() - timedelta(days=31)
        path = (await s.scalar(select(LeadFile))).path
        await s.commit()
    assert Path(path).exists()
    assert await a.purge_files() == 1
    assert not Path(path).exists()
    async with priv.sf() as s:
        assert (await s.scalar(select(LeadFile))).path is None
    assert await a.purge_files() == 0


async def test_consent_is_asked_with_the_application_and_counts_when_volunteered(priv):
    a = priv.agent
    for i, x in enumerate(["Turkiyada ish beruvchim bor, ishlash uchun hujjatlar kerak", "1 kishi", "Ismim Aziz", "+998 90 123 45 67",
                           "Istanbul, Kadıköy. Ha, yubora oladi", "ha"]):
        r = await a.handle("telegram", "c1", x, message_id=f"c{i}")
    assert r.won and "Rozimisiz" in r.text and r.language == "uz"      # a Turkish place name did not switch the language
    r = await a.handle("telegram", "c1", "Roziman", message_id="c9")
    assert r.text.startswith("Rahmat! Endi hujjatlarni shu yerga yuboring")
    r = await a.handle_file("telegram", "c1", kind="document", data=b"%PDF", file_name="vergi_levhasi.pdf", message_id="cf")
    assert "Qabul qilingan: Vergi levhası" in r.text
