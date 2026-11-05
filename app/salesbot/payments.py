"""Advance payment instructions after a confirmed application.

Providers:
- card:  manual transfer to SALES_PAYMENT_CARD; a manager confirms with /paid <lead>
- payme: checkout.paycom.uz link (amount in tiyin, converted from USD with SALES_USD_RATE)
- click: my.click.uz link
- link:  any URL from SALES_PAYMENT_LINK_TEMPLATE
Payment provider callbacks (merchant API) are not handled here: a manager
marks the payment with /paid after seeing it in the cabinet.
"""
from __future__ import annotations

import base64
import math

from app.salesbot import lang as L
from app.salesbot.catalog import SOM, money


def to_uzs(amount: int, currency: str | None, usd_rate: float | None) -> int | None:
    cur = (currency or "so'm").lower()
    if cur in SOM:
        return int(amount)
    if cur in ("usd", "$") and usd_rate:
        return int(math.ceil(amount * usd_rate / 1000.0) * 1000)  # round up to 1 000 so'm
    return None


def payment_link(settings, lead_id: int, amount: int, currency: str | None) -> str | None:
    provider = (settings.sales_payment_provider or "none").lower()
    uzs = to_uzs(amount, currency, settings.sales_usd_rate)
    if provider == "payme" and settings.payme_merchant_id and uzs:
        raw = f"m={settings.payme_merchant_id};ac.{settings.payme_account_field}={lead_id};a={uzs * 100}"
        return "https://checkout.paycom.uz/" + base64.b64encode(raw.encode()).decode()
    if provider == "click" and settings.click_service_id and settings.click_merchant_id and uzs:
        return (f"https://my.click.uz/services/pay?service_id={settings.click_service_id}&merchant_id={settings.click_merchant_id}"
                f"&amount={uzs}&transaction_param={lead_id}")
    if provider == "link" and settings.sales_payment_link_template:
        return settings.sales_payment_link_template.format(lead_id=lead_id, amount=amount, currency=currency or "", amount_uzs=uzs or "")
    return None


def payment_text(settings, lang: str, lead_id: int, amount: int, currency: str | None, link: str | None) -> str | None:
    """What the client reads under the confirmation, or None when no payment method is configured."""
    shown = money(amount, currency or "so'm", lang)
    uzs = to_uzs(amount, currency, settings.sales_usd_rate)
    if uzs and (currency or "").lower() not in SOM:
        shown += " (≈ " + money(uzs, "so'm", lang) + ")"
    if link:
        return L.text(lang, "pay_link", amount=shown, link=link)
    if (settings.sales_payment_provider or "").lower() == "card" and settings.sales_payment_card:
        return L.text(lang, "pay_card", amount=shown, card=settings.sales_payment_card, id=lead_id)
    return None
