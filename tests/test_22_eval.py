"""Sifat tekshiruvi: barcha namunaviy suhbatlar qoidalar rejimida o'tishi kerak (regressiya to'plami)."""
from pathlib import Path

from app.salesbot.evaluation import report, run
from tests.conftest import make_settings

ROOT = Path(__file__).resolve().parents[1]


async def test_all_eval_cases_pass_in_rules_mode(tmp_path):
    settings = make_settings(tmp_path, sales_catalog_path=str(ROOT / "sales_catalog.json"), sales_faq_path=str(ROOT / "sales_faq.json"),
                             sales_catalog_from_books=False, sales_shop_name="Jihan Jan Jasur Nam Tour")
    results = await run(settings, ROOT / "sales_eval_cases.json")
    assert len(results) >= 25
    assert all(r.ok for r in results), report(results)


class InventingModel:
    """An AI that invents a price and a guarantee: the evaluation must catch both."""

    async def analyse(self, **kw):
        return {"reply": "Narxi 999 $, 100% kafolat beramiz!", "product_id": "", "quantity": 0, "name": "", "phone": "", "address": "",
                "pickup": False, "confirm": False, "decline": False, "operator": False, "discount": False}


async def test_eval_catches_invented_prices_and_guarantees(tmp_path):
    settings = make_settings(tmp_path, sales_catalog_path=str(ROOT / "sales_catalog.json"), sales_faq_path=None, sales_catalog_from_books=False)
    results = await run(settings, ROOT / "sales_eval_cases.json", model=InventingModel())
    text = report(results)
    assert "каталогда йўқ нарх: 999 $" in text and "кафолат ваъда қилинди" in text
