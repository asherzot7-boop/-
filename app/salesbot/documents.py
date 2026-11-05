"""Contract and invoice PDFs for a confirmed application (fpdf2, Unicode TTF font).

The contract text comes from sales_contract_template.txt so a lawyer can edit it
without touching code. Fonts: SALES_PDF_FONT, else Arial on Windows, else DejaVu.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.salesbot import catalog as cat
from app.salesbot import payments

log = logging.getLogger(__name__)

FONT_CANDIDATES = [r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]


def find_font(configured: str | None) -> str | None:
    for p in ([configured] if configured else []) + FONT_CANDIDATES:
        if p and Path(p).exists():
            return p
    return None


def _fields(lead, product, settings) -> dict:
    lg = "uz"
    total = cat.price_text(product, lg, lead.total) if product and lead.total else "—"
    prepay = cat.money(lead.prepayment_due, lead.currency or "so'm", lg) if lead.prepayment_due else "—"
    uzs = payments.to_uzs(lead.prepayment_due or 0, lead.currency, settings.sales_usd_rate) if lead.prepayment_due else None
    if uzs and (lead.currency or "").lower() not in cat.SOM:
        prepay += f" (≈ {cat.money(uzs, 'so' + chr(39) + 'm', lg)})"
    details = [settings.sales_company_address, f"STIR: {settings.sales_company_inn}" if settings.sales_company_inn else None,
               settings.sales_company_bank, settings.sales_company_phone]
    return {
        "number": f"{lead.id}/{datetime.now():%Y}", "date": datetime.now(settings.tz).strftime("%d.%m.%Y"), "city": settings.sales_company_city,
        "company": settings.sales_company_name, "company_details": "\n".join(x for x in details if x) or "—",
        "client": lead.name or "—", "phone": lead.phone or "—", "service": (product.title("uz") if product else lead.product_name) or "—",
        "qty": lead.quantity or 1, "total": total, "prepayment": prepay, "details": lead.address or "—",
        "card": settings.sales_payment_card or "—", "link": lead.payment_link or "",
    }


def _pdf(font: str):
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.add_font("Body", "", font)
    pdf.add_page()
    pdf.set_font("Body", size=10.5)
    return pdf


def contract_pdf(lead, product, settings) -> bytes | None:
    font = find_font(settings.sales_pdf_font)
    tpl = Path(settings.sales_contract_template)
    if not font or not tpl.exists():
        log.warning("Шартнома тузилмади: шрифт ёки шаблон топилмади")
        return None
    text = tpl.read_text(encoding="utf-8").format(**_fields(lead, product, settings))
    pdf = _pdf(font)
    lines = text.splitlines()
    pdf.set_font("Body", size=13)
    pdf.multi_cell(0, 7, lines[0], align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Body", size=10.5)
    for line in lines[1:]:
        pdf.multi_cell(0, 5.6, line or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def invoice_pdf(lead, product, settings) -> bytes | None:
    font = find_font(settings.sales_pdf_font)
    if not font:
        log.warning("Ҳисоб тузилмади: шрифт топилмади (SALES_PDF_FONT)")
        return None
    f = _fields(lead, product, settings)
    pdf = _pdf(font)
    pdf.set_font("Body", size=15)
    pdf.cell(0, 9, f"TO'LOV HISOBI № {f['number']}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Body", size=10)
    pdf.cell(0, 6, f"Sana: {f['date']}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.multi_cell(0, 5.5, f"Ijrochi: {f['company']}\n{f['company_details']}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.multi_cell(0, 5.5, f"Mijoz: {f['client']}, {f['phone']}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    widths = (95, 25, 54)
    pdf.set_fill_color(230, 238, 247)
    for w, h in zip(widths, ("Xizmat", "Soni", "Summa")):
        pdf.cell(w, 8, h, border=1, fill=True)
    pdf.ln()
    pdf.multi_cell(widths[0], 8, f["service"], border=1, new_x="RIGHT", new_y="TOP", max_line_height=pdf.font_size * 1.4)
    y_after = pdf.get_y()
    pdf.cell(widths[1], 8, str(f["qty"]), border=1)
    pdf.cell(widths[2], 8, f["total"], border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(max(pdf.get_y(), y_after + 8))
    pdf.ln(4)
    pdf.set_font("Body", size=12)
    pdf.cell(0, 7, f"Hozir to'lanadi (oldindan to'lov): {f['prepayment']}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Body", size=10)
    pdf.ln(2)
    if f["link"]:
        pdf.multi_cell(0, 5.5, f"To'lov havolasi: {f['link']}", new_x="LMARGIN", new_y="NEXT")
    if f["card"] != "—":
        pdf.multi_cell(0, 5.5, f"Karta: {f['card']}. Izohda hisob raqamini yozing: № {lead.id}.", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
