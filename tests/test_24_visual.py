"""Визуал саҳифалар /visual/ орқали очилади; фақат visual/ папкасидаги саҳифалар берилади."""
from fastapi.testclient import TestClient

from app.api.routes import create_api
from tests.conftest import build_env, make_settings


async def test_visual_pages_are_served(tmp_path):
    e = await build_env(make_settings(tmp_path))
    client = TestClient(create_api(e.settings, e.sf))
    for root in ("/visual", "/visual/"):
        assert client.get(root, follow_redirects=False).headers["location"] == "/visual/journeys-3d.html"
    for name in ("journeys.html", "journeys-3d.html", "agent.html", "agent-3d-variants.html", "hologram.html"):
        r = client.get(f"/visual/{name}")
        assert r.status_code == 200 and r.text.startswith("<!doctype html>"), name
    assert client.get("/visual/index.html").status_code == 404
    assert client.get("/visual/..%2Fsales_catalog.json").status_code == 404
    await e.engine.dispose()
