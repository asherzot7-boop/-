"""Automatic advance-payment confirmation: Payme Merchant API and Click SHOP API.

Payme calls POST /webhooks/payme (JSON-RPC 2.0, Basic auth "Paycom:<PAYME_KEY>").
Click calls POST /webhooks/click/prepare and /webhooks/click/complete (form data, md5 sign).
Both check that the order (lead id) exists, is a confirmed application with an
advance payment, is not paid yet, and that the amount equals lead.extra["pay_uzs"].
A performed / completed transaction marks the lead paid exactly like /paid does.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.salesbot.models import Lead, LeadStage, PaymentTx

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks")

PAYME_TIMEOUT_MS = 12 * 60 * 60 * 1000      # Payme cancels a created-but-not-performed transaction after 12 h


def _ms() -> int:
    return int(time.time() * 1000)


def _msg(ru: str, uz: str, en: str) -> dict:
    return {"ru": ru, "uz": uz, "en": en}


# ------------------------------------------------------------------ Payme
class PaymeError(Exception):
    def __init__(self, code: int, message: dict, data: str | None = None):
        self.code, self.message, self.data = code, message, data


ERR_ORDER = PaymeError(-31050, _msg("Заказ не найден", "Buyurtma topilmadi", "Order not found"), "order_id")
ERR_PAID = PaymeError(-31051, _msg("Заказ уже оплачен", "Buyurtma allaqachon to'langan", "Order already paid"), "order_id")
ERR_BUSY = PaymeError(-31052, _msg("Заказ ожидает другую оплату", "Buyurtma boshqa to'lovni kutmoqda", "Order is awaiting another payment"), "order_id")
ERR_AMOUNT = PaymeError(-31001, _msg("Неверная сумма", "Noto'g'ri summa", "Incorrect amount"))
ERR_NO_TX = PaymeError(-31003, _msg("Транзакция не найдена", "Tranzaksiya topilmadi", "Transaction not found"))
ERR_CANT = PaymeError(-31008, _msg("Невозможно выполнить операцию", "Amalni bajarib bo'lmaydi", "Unable to perform operation"))


def _payme_auth_ok(request: Request, key: str) -> bool:
    header = request.headers.get("authorization", "")
    if not key or not header.startswith("Basic "):
        return False
    try:
        login, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:
        return False
    return login == "Paycom" and hmac.compare_digest(password, key)


async def _order(s, settings, account: dict, amount: int | None) -> Lead:
    raw = (account or {}).get(settings.payme_account_field)
    try:
        lead = await s.get(Lead, int(raw))
    except (TypeError, ValueError):
        raise ERR_ORDER from None
    if lead is None or lead.stage != LeadStage.WON or not (lead.extra or {}).get("pay_uzs"):
        raise ERR_ORDER
    if lead.prepaid_at is not None:
        raise ERR_PAID
    if amount is not None and int(amount) != int(lead.extra["pay_uzs"]) * 100:
        raise ERR_AMOUNT
    return lead


def _tx_result(tx: PaymentTx) -> dict:
    return {"create_time": tx.create_time, "perform_time": tx.perform_time, "cancel_time": tx.cancel_time,
            "transaction": str(tx.id), "state": tx.state, "reason": tx.reason}


@router.post("/payme")
async def payme(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    key = settings.payme_key.get_secret_value() if settings.payme_key else ""
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": {"code": -32700, "message": _msg("Ошибка разбора JSON", "JSON xatosi", "Parse error")}, "id": None})
    rid = body.get("id")
    if not _payme_auth_ok(request, key):
        return JSONResponse({"error": {"code": -32504, "message": _msg("Недостаточно привилегий", "Ruxsat yo'q", "Insufficient privileges")}, "id": rid})
    method, params = body.get("method"), body.get("params") or {}
    agent = request.app.state.sales
    try:
        async with request.app.state.session_factory() as s:
            try:
                result, paid_lead = await _payme_method(s, settings, method, params)
            except PaymeError:
                await s.commit()        # keep state changes made before the error (e.g. a timeout cancellation)
                raise
            await s.commit()
        if paid_lead is not None and agent is not None:
            await agent.mark_paid(paid_lead)
        return JSONResponse({"result": result, "id": rid})
    except PaymeError as e:
        err = {"code": e.code, "message": e.message}
        if e.data:
            err["data"] = e.data
        return JSONResponse({"error": err, "id": rid})


async def _payme_method(s, settings, method: str, p: dict):
    now = _ms()
    if method == "CheckPerformTransaction":
        await _order(s, settings, p.get("account"), p.get("amount"))
        return {"allow": True}, None
    if method == "CreateTransaction":
        tx = await s.scalar(select(PaymentTx).where(PaymentTx.provider == "payme", PaymentTx.external_id == str(p.get("id"))))
        if tx is not None:
            if tx.state != 1:
                raise ERR_CANT
            if now - tx.create_time > PAYME_TIMEOUT_MS:
                tx.state, tx.cancel_time, tx.reason = -1, now, 4
                raise ERR_CANT
            return {"create_time": tx.create_time, "transaction": str(tx.id), "state": tx.state}, None
        lead = await _order(s, settings, p.get("account"), p.get("amount"))
        other = await s.scalar(select(PaymentTx).where(PaymentTx.provider == "payme", PaymentTx.lead_id == lead.id, PaymentTx.state == 1))
        if other is not None:
            raise ERR_BUSY
        tx = PaymentTx(provider="payme", external_id=str(p.get("id")), lead_id=lead.id, amount=int(p["amount"]), state=1,
                       create_time=int(p.get("time") or now))
        s.add(tx)
        await s.flush()
        return {"create_time": tx.create_time, "transaction": str(tx.id), "state": 1}, None
    tx = await s.scalar(select(PaymentTx).where(PaymentTx.provider == "payme", PaymentTx.external_id == str(p.get("id"))))
    if method == "PerformTransaction":
        if tx is None:
            raise ERR_NO_TX
        if tx.state == 2:
            return {"transaction": str(tx.id), "perform_time": tx.perform_time, "state": 2}, None
        if tx.state != 1:
            raise ERR_CANT
        if now - tx.create_time > PAYME_TIMEOUT_MS:
            tx.state, tx.cancel_time, tx.reason = -1, now, 4
            raise ERR_CANT
        tx.state, tx.perform_time = 2, now
        return {"transaction": str(tx.id), "perform_time": now, "state": 2}, tx.lead_id
    if method == "CancelTransaction":
        if tx is None:
            raise ERR_NO_TX
        if tx.state in (1, 2):
            tx.state = -1 if tx.state == 1 else -2
            tx.cancel_time, tx.reason = now, int(p.get("reason") or 0)
            if tx.state == -2:
                lead = await s.get(Lead, tx.lead_id)
                lead.prepaid_at = None          # refunded: the advance is no longer paid
                lead.extra = {**(lead.extra or {}), "refunded": True}
        return {"transaction": str(tx.id), "cancel_time": tx.cancel_time, "state": tx.state}, None
    if method == "CheckTransaction":
        if tx is None:
            raise ERR_NO_TX
        return _tx_result(tx), None
    if method == "GetStatement":
        rows = await s.scalars(select(PaymentTx).where(PaymentTx.provider == "payme", PaymentTx.create_time >= int(p.get("from", 0)),
                                                        PaymentTx.create_time <= int(p.get("to", now))).order_by(PaymentTx.create_time))
        return {"transactions": [{"id": t.external_id, "time": t.create_time, "amount": t.amount,
                                  "account": {settings.payme_account_field: str(t.lead_id)}, **_tx_result(t)} for t in rows]}, None
    raise PaymeError(-32601, _msg("Метод не найден", "Metod topilmadi", "Method not found"))


# ------------------------------------------------------------------ Click
def _click_sign(p: dict, secret: str, with_prepare: bool) -> str:
    parts = [p.get("click_trans_id", ""), p.get("service_id", ""), secret, p.get("merchant_trans_id", "")]
    if with_prepare:
        parts.append(p.get("merchant_prepare_id", ""))
    parts += [p.get("amount", ""), p.get("action", ""), p.get("sign_time", "")]
    return hashlib.md5("".join(str(x) for x in parts).encode()).hexdigest()


def _click_reply(p: dict, error: int, note: str, **extra) -> JSONResponse:
    return JSONResponse({"click_trans_id": p.get("click_trans_id"), "merchant_trans_id": p.get("merchant_trans_id"),
                         "error": error, "error_note": note, **extra})


async def _click_common(request: Request, complete: bool):
    settings = request.app.state.settings
    p = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    secret = settings.click_secret_key.get_secret_value() if settings.click_secret_key else ""
    if not secret or not hmac.compare_digest(_click_sign(p, secret, complete), str(p.get("sign_string", ""))):
        return p, None, _click_reply(p, -1, "SIGN CHECK FAILED!")
    if str(p.get("action")) != ("1" if complete else "0"):
        return p, None, _click_reply(p, -3, "Action not found")
    return p, settings, None


@router.post("/click/prepare")
async def click_prepare(request: Request) -> JSONResponse:
    p, settings, err = await _click_common(request, complete=False)
    if err is not None:
        return err
    async with request.app.state.session_factory() as s:
        try:
            lead = await s.get(Lead, int(p.get("merchant_trans_id", "x")))
        except ValueError:
            lead = None
        if lead is None or lead.stage != LeadStage.WON or not (lead.extra or {}).get("pay_uzs"):
            return _click_reply(p, -5, "User does not exist")
        if lead.prepaid_at is not None:
            return _click_reply(p, -4, "Already paid")
        if round(float(p.get("amount", "0")), 2) != float(lead.extra["pay_uzs"]):
            return _click_reply(p, -2, "Incorrect parameter amount")
        tx = await s.scalar(select(PaymentTx).where(PaymentTx.provider == "click", PaymentTx.external_id == str(p["click_trans_id"])))
        if tx is None:
            tx = PaymentTx(provider="click", external_id=str(p["click_trans_id"]), lead_id=lead.id, amount=int(lead.extra["pay_uzs"]),
                           state=1, create_time=_ms())
            s.add(tx)
            await s.flush()
        await s.commit()
        return _click_reply(p, 0, "Success", merchant_prepare_id=tx.id)


@router.post("/click/complete")
async def click_complete(request: Request) -> JSONResponse:
    p, settings, err = await _click_common(request, complete=True)
    if err is not None:
        return err
    paid_lead = None
    async with request.app.state.session_factory() as s:
        try:
            tx = await s.get(PaymentTx, int(p.get("merchant_prepare_id", "x")))
        except ValueError:
            tx = None
        if tx is None or tx.provider != "click" or tx.external_id != str(p.get("click_trans_id")):
            return _click_reply(p, -6, "Transaction does not exist")
        if tx.state == -1:
            return _click_reply(p, -9, "Transaction cancelled")
        if int(p.get("error", "0") or 0) < 0:                 # Click reports a failed payment: cancel ours
            tx.state, tx.cancel_time = -1, _ms()
            await s.commit()
            return _click_reply(p, -9, "Transaction cancelled")
        if tx.state == 2:
            return _click_reply(p, -4, "Already paid", merchant_confirm_id=tx.id)
        if round(float(p.get("amount", "0")), 2) != float(tx.amount):
            return _click_reply(p, -2, "Incorrect parameter amount")
        tx.state, tx.perform_time = 2, _ms()
        paid_lead = tx.lead_id
        await s.commit()
    agent = request.app.state.sales
    if agent is not None and paid_lead is not None:
        await agent.mark_paid(paid_lead)
    return _click_reply(p, 0, "Success", merchant_confirm_id=tx.id)
