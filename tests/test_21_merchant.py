"""Payme Merchant API va Click SHOP API: avtomatik to'lov tasdig'i."""
import base64
import hashlib
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.api.routes import create_api
from app.notify import ConsoleNotifier
from app.salesbot.agent import SalesAgent
from app.salesbot.models import Lead
from app.salesbot.outbound import Outbound
from tests.conftest import build_env, make_settings

ROOT = Path(__file__).resolve().parents[1]
AUTH = {"Authorization": "Basic " + base64.b64encode(b"Paycom:payme-secret").decode()}


class Sink:
    def __init__(self):
        self.sent, self.docs = [], []

    async def send(self, channel, recipient, text, extra=None):
        self.sent.append(text)
        return True

    async def send_document(self, channel, recipient, data, filename, caption, extra=None):
        self.docs.append((filename, data[:4]))
        return True


@pytest.fixture
async def pay(tmp_path):
    settings = make_settings(tmp_path, sales_catalog_path=str(ROOT / "sales_catalog.json"), sales_faq_path=None, sales_catalog_from_books=False,
                             sales_consent_required=False, sales_usd_rate=12800, sales_payment_provider="payme", payme_merchant_id="MID",
                             payme_key="payme-secret", click_secret_key="click-secret", sales_company_inn="305000000")
    e = await build_env(settings)
    e.sink = Sink()
    out = Outbound(); out.register(("telegram",), e.sink)
    e.agent = SalesAgent(e.sf, settings, notifier=ConsoleNotifier(), outbound=out)
    for i, x in enumerate(["Deportim bor 3 yillik, narxi qancha?", "Ismim Bek", "+998901112233", "2023, 3 yil", "ha"]):
        r = await e.agent.handle("telegram", "1", x, message_id=str(i))
    e.won = r
    e.client = TestClient(create_api(settings, e.sf, sales=e.agent))
    yield e
    await e.engine.dispose()


def rpc(c, method, params, auth=AUTH):
    return c.post("/webhooks/payme", headers=auth, json={"method": method, "params": params, "id": 7}).json()


async def test_documents_go_to_the_client_after_confirmation(pay):
    assert "checkout.paycom.uz" in pay.won.text
    assert [d[0] for d in pay.sink.docs] == ["hisob_1.pdf", "shartnoma_1.pdf"] and all(d[1] == b"%PDF" for d in pay.sink.docs)


async def test_payme_full_cycle(pay):
    c, amount = pay.client, 600 * 12800 * 100
    assert rpc(c, "CheckPerformTransaction", {"amount": amount, "account": {"order_id": "1"}}, auth={"Authorization": "Basic eDp5"})["error"]["code"] == -32504
    assert rpc(c, "CheckPerformTransaction", {"amount": amount + 100, "account": {"order_id": "1"}})["error"]["code"] == -31001
    assert rpc(c, "CheckPerformTransaction", {"amount": amount, "account": {"order_id": "99"}})["error"]["code"] == -31050
    assert rpc(c, "CheckPerformTransaction", {"amount": amount, "account": {"order_id": "1"}})["result"] == {"allow": True}
    created = rpc(c, "CreateTransaction", {"id": "pm-1", "time": int(time.time() * 1000), "amount": amount, "account": {"order_id": "1"}})["result"]
    assert created["state"] == 1
    assert rpc(c, "CreateTransaction", {"id": "pm-2", "time": 1, "amount": amount, "account": {"order_id": "1"}})["error"]["code"] == -31052
    performed = c.post("/webhooks/payme", headers=AUTH, json={"method": "PerformTransaction", "params": {"id": "pm-1"}, "id": 8}).json()["result"]
    assert performed["state"] == 2
    async with pay.sf() as s:
        assert (await s.get(Lead, 1)).prepaid_at is not None
    assert any("To'lovingiz qabul qilindi" in t for t in pay.sink.sent)
    assert rpc(c, "PerformTransaction", {"id": "pm-1"})["result"]["state"] == 2           # idempotent
    assert rpc(c, "CheckTransaction", {"id": "pm-1"})["result"]["state"] == 2
    assert rpc(c, "CheckPerformTransaction", {"amount": amount, "account": {"order_id": "1"}})["error"]["code"] == -31051
    assert rpc(c, "CancelTransaction", {"id": "pm-1", "reason": 5})["result"]["state"] == -2
    async with pay.sf() as s:
        assert (await s.get(Lead, 1)).prepaid_at is None
    assert len(rpc(c, "GetStatement", {"from": 0, "to": 9_999_999_999_999})["result"]["transactions"]) == 1


async def test_payme_cancels_a_transaction_older_than_12_hours(pay):
    c, amount = pay.client, 600 * 12800 * 100
    rpc(c, "CreateTransaction", {"id": "old", "time": int(time.time() * 1000) - 13 * 3600 * 1000, "amount": amount, "account": {"order_id": "1"}})
    assert rpc(c, "PerformTransaction", {"id": "old"})["error"]["code"] == -31008
    check = rpc(c, "CheckTransaction", {"id": "old"})["result"]
    assert check["state"] == -1 and check["reason"] == 4


def click_form(action, extra, secret="click-secret"):
    p = {"click_trans_id": "555", "service_id": "11", "click_paydoc_id": "1", "merchant_trans_id": "1", "amount": f"{600 * 12800}.00",
         "action": str(action), "error": "0", "error_note": "Success", "sign_time": "2026-11-05 10:00:00", **extra}
    parts = [p["click_trans_id"], p["service_id"], secret, p["merchant_trans_id"]] + ([p["merchant_prepare_id"]] if action == 1 else []) + [p["amount"], p["action"], p["sign_time"]]
    p["sign_string"] = hashlib.md5("".join(parts).encode()).hexdigest()
    return urlencode(p)


async def test_click_prepare_and_complete(pay):
    c = pay.client
    h = {"Content-Type": "application/x-www-form-urlencoded"}
    assert c.post("/webhooks/click/prepare", content=click_form(0, {}, secret="wrong"), headers=h).json()["error"] == -1
    prep = c.post("/webhooks/click/prepare", content=click_form(0, {}), headers=h).json()
    assert prep["error"] == 0 and prep["merchant_prepare_id"]
    done = c.post("/webhooks/click/complete", content=click_form(1, {"merchant_prepare_id": str(prep["merchant_prepare_id"])}), headers=h).json()
    assert done["error"] == 0
    async with pay.sf() as s:
        assert (await s.get(Lead, 1)).prepaid_at is not None
    again = c.post("/webhooks/click/complete", content=click_form(1, {"merchant_prepare_id": str(prep["merchant_prepare_id"])}), headers=h).json()
    assert again["error"] == -4
