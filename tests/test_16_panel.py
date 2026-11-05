"""Panel: uланишлар, раҳбар, агент иш жараёни, менежер ва менежер амаллари."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import create_api
from app.notify import ConsoleNotifier
from app.salesbot.agent import SalesAgent
from app.salesbot.outbound import Outbound
from tests.conftest import build_env, make_settings

ROOT = Path(__file__).resolve().parents[1]
TOKEN = {"X-API-Token": "panel-secret"}


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, channel, recipient, text, extra=None):
        self.sent.append((channel, recipient, text))
        return True


@pytest.fixture
async def panel(tmp_path):
    settings = make_settings(tmp_path, api_token="panel-secret", sales_catalog_path=str(ROOT / "sales_catalog.json"),
                             sales_faq_path=str(ROOT / "sales_faq.json"), sales_catalog_from_books=False, sales_manager_ids=[777],
                             sales_files_dir=str(tmp_path / "files"), sales_payment_card="8600 0000 0000 0000", sales_usd_rate=12800,
                             sales_bot_token="123:SECRET-TELEGRAM", backup_dir=str(tmp_path / "backups"))
    e = await build_env(settings)
    e.sender = FakeSender()
    out = Outbound()
    out.register(("instagram", "whatsapp", "telegram"), e.sender)
    e.agent = SalesAgent(e.sf, settings, notifier=ConsoleNotifier(), outbound=out)
    e.client = TestClient(create_api(settings, e.sf, sales=e.agent))
    a = e.agent
    for i, line in enumerate(["Deportim bor 3 yillik, narxi qancha?", "Ismim Bekzod", "+998 91 222 33 44", "2025-yil mart, 3 yilga", "ha"]):
        await a.handle("instagram", "bek", line, message_id=f"b{i}")
    for i, line in enumerate(["Pasportni tekshirish kerak", "1", "Ismim Jasur", "+998 90 777 66 55", "Ertaga", "ha"]):
        await a.handle("telegram", "42", line, message_id=f"j{i}")
    await a.handle("whatsapp", "998935550000", "Merhaba, ofisiniz nerede?", message_id="w1")
    await a.handle("instagram", "op", "Operator bilan gaplashmoqchiman", message_id="o1")
    await a.handle_file("telegram", "42", kind="document", data=b"%PDF-1.4", file_name="pasport.pdf", mime="application/pdf", message_id="f1")
    yield e
    await e.engine.dispose()


def test_page_is_public_but_data_needs_the_token(panel):
    assert panel.client.get("/panel").status_code == 200
    assert "Nam Tour панели" in panel.client.get("/panel").text
    assert panel.client.get("/api/panel/director").status_code == 401
    assert panel.client.get("/api/panel/director", headers={"X-API-Token": "wrong"}).status_code == 401


def test_connections_show_status_never_secrets(panel):
    resp = panel.client.get("/api/panel/connections", headers=TOKEN)
    items = {i["key"]: i for i in resp.json()}
    assert "SECRET-TELEGRAM" not in resp.text and "8600 0000" not in resp.text
    assert items["tg_client"]["status"] == "ok" and items["tg_client"]["last_activity"]
    assert items["instagram"]["status"] == "off" and "INSTAGRAM_ACCESS_TOKEN" in items["instagram"]["missing"]
    assert items["payment"]["status"] == "ok" and "12 800" in items["payment"]["detail"]
    assert items["backup"]["status"] == "off"


def test_director_numbers(panel):
    d = panel.client.get("/api/panel/director?days=30", headers=TOKEN).json()
    t = d["totals"]
    assert (t["leads"], t["won"], t["conversion"], t["handoff"]) == (4, 2, 50, 1)
    assert d["revenue"] == [{"currency": "USD", "amount": 2680, "text": "2 680 $"}]
    assert d["prepay_due"][0]["amount"] == 600 and d["prepaid"] == []
    channels = {c["name"]: c for c in d["by_channel"]}
    assert channels["Instagram"] == {"name": "Instagram", "leads": 2, "won": 1}
    assert d["funnel"][0]["count"] == 4 and d["funnel"][-1]["count"] == 2
    assert sum(x["leads"] for x in d["daily"]) == 4


def test_agent_process(panel):
    a = panel.client.get("/api/panel/agent", headers=TOKEN).json()
    assert a["mode"] == "Қоидалар режими"
    assert a["today"]["files"] == 1 and a["today"]["in"] >= 13 and a["today"]["won"] == 2
    assert a["waiting"] == {"handoff": 1, "questions": 1}
    assert a["feed"][0]["lead_id"] and {m["direction"] for m in a["feed"]} == {"in", "out"}


def test_manager_queues_and_actions(panel):
    m = panel.client.get("/api/panel/manager", headers=TOKEN).json()
    assert [c["who"] for c in m["payments"]] == ["Bekzod"]
    assert [c["who"] for c in m["results"]] == ["Jasur"] and m["results"][0]["files"][0]["name"] == "pasport.pdf"
    assert len(m["handoff"]) == 1 and len(m["questions"]) == 1 and m["questions"][0]["question"].startswith("Merhaba")

    bek = m["payments"][0]["id"]
    r = panel.client.post(f"/api/panel/leads/{bek}/paid", headers=TOKEN).json()
    assert r["ok"] and "To'lovingiz qabul qilindi" in panel.sender.sent[-1][2]

    jasur = m["results"][0]["id"]
    r = panel.client.post(f"/api/panel/leads/{jasur}/result", headers=TOKEN, json={"outcome": "ban", "note": "5 yilga"}).json()
    assert r["ok"] and "Deportni olib tashlash" in panel.sender.sent[-1][2]

    q = m["questions"][0]["id"]
    r = panel.client.post(f"/api/panel/leads/{q}/answer", headers=TOKEN, json={"text": "Ofisimiz Toshkentda, Chilonzor 5."}).json()
    assert r["ok"] and "базага" in r["message"]

    m2 = panel.client.get("/api/panel/manager", headers=TOKEN).json()
    assert m2["payments"] == [] and m2["results"] == [] and m2["questions"] == []

    detail = panel.client.get(f"/api/panel/leads/{q}", headers=TOKEN).json()
    assert detail["messages"][-1]["text"].endswith("Chilonzor 5.")
    assert panel.client.post(f"/api/panel/leads/{q}/reply", headers=TOKEN, json={"text": ""}).status_code == 422


def test_files_are_served_only_from_the_files_folder(panel):
    m = panel.client.get("/api/panel/manager", headers=TOKEN).json()
    fid = m["results"][0]["files"][0]["id"]
    resp = panel.client.get(f"/api/panel/files/{fid}", headers=TOKEN)
    assert resp.status_code == 200 and resp.content == b"%PDF-1.4"
    assert panel.client.get(f"/api/panel/files/{fid}").status_code == 401
    assert panel.client.get("/api/panel/files/999", headers=TOKEN).status_code == 404
