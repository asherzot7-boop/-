"""Визуал саҳифалар: /visual/ орқали очилади ва демо суҳбатлар ҳақиқий агентдан йиғилади."""
from fastapi.testclient import TestClient

from app.api.routes import create_api
from app.salesbot import journeys
from tests.conftest import build_env, make_settings


async def test_visual_pages_are_served(tmp_path):
    e = await build_env(make_settings(tmp_path))
    client = TestClient(create_api(e.settings, e.sf))
    assert client.get("/visual", follow_redirects=False).headers["location"] == "/visual/"
    index = client.get("/visual/")
    assert index.status_code == 200 and "journeys-3d.html" in index.text and "/weekly" in index.text
    for name in ("journeys.html", "journeys-3d.html", "agent.html", "agent-3d-variants.html", "hologram.html"):
        r = client.get(f"/visual/{name}")
        assert r.status_code == 200 and r.text.startswith("<!doctype html>"), name
    assert client.get("/visual/templates/journeys.html").status_code == 404
    assert client.get("/visual/..%2Fsales_catalog.json").status_code == 404
    await e.engine.dispose()


async def test_journeys_are_recorded_from_the_real_agent(tmp_path):
    events = await journeys.record()
    assert set(events) == {"passport", "deport", "work"}
    tags = {e["tag"] for evs in events.values() for e in evs}
    assert {"Payme", "Согласие", "Чек-лист документов", "Напоминание", "Допродажа", "Без обещаний гарантий", "Недельный отчёт"} <= tags
    deport = " ".join(e["text"] for e in events["deport"] if e["who"] == "agent")
    assert "2 600 $" in deport and "600 $" in deport
    written = journeys.render(events, tmp_path)
    assert all("__DATA__" not in p.read_text(encoding="utf-8") and "Dilnoza" in p.read_text(encoding="utf-8") for p in written)
