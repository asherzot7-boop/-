"""Компьютерга ўрнатиладиган дастур: .env билан ишлаш, /setup саҳифаси ва ишга туширувчи."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import desktop, envfile
from app import main as app_main
from app.api.routes import create_api
from app.config import Settings
from tests.conftest import build_env, make_settings

ROOT = Path(__file__).resolve().parents[1]


def test_envfile_keeps_comments_and_quotes_values(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# izoh\nA=1\nB=\n", encoding="utf-8")
    envfile.update(p, {"B": 'karta "8600" (Jasur N.)', "C": "x y", "A": "2"})
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# izoh\nA=2\n")
    assert envfile.read(p) == {"A": "2", "B": 'karta "8600" (Jasur N.)', "C": "x y"}
    with pytest.raises(ValueError):
        envfile.update(p, {"A": "two\nlines"})


def test_env_example_loads_as_is():
    s = Settings(_env_file=str(ROOT / ".env.example"))      # empty SALES_USD_RATE= used to stop the agent
    assert s.sales_usd_rate is None and s.api_token is None


def test_run_mode_is_not_shadowed_by_eval_import():
    assert "run" not in app_main.main.__code__.co_varnames   # `python -m app.main` crashed with UnboundLocalError


def test_first_run_creates_env_and_keeps_client_values(tmp_path):
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    env = desktop.ensure_env(tmp_path)
    assert env["API_HOST"] == "127.0.0.1" and len(env["API_TOKEN"]) >= 24 and len(env["SALES_FILES_KEY"]) == 44
    envfile.update(tmp_path / ".env", {"SALES_BOT_TOKEN": "1:x"})
    again = desktop.ensure_env(tmp_path)
    assert again["API_TOKEN"] == env["API_TOKEN"] and again["SALES_BOT_TOKEN"] == "1:x"
    assert desktop.configured(again) and not desktop.configured(env)


def test_open_goes_to_setup_first_then_to_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "healthy", lambda port, timeout=2.0: True)
    opened = []
    url = desktop.open_app(tmp_path, browser=opened.append)
    assert url.startswith("http://127.0.0.1:8000/setup#token=")
    envfile.update(tmp_path / ".env", {"SALES_BOT_TOKEN": "1:x"})
    assert desktop.open_app(tmp_path, browser=opened.append).startswith("http://127.0.0.1:8000/panel#token=")


def test_keeper_restarts_after_settings_change_and_stops_on_request(tmp_path, monkeypatch):
    codes = [desktop.RESTART_CODE, 1, 0]

    class Child:
        pid = 4242

        def wait(self):
            code = codes.pop(0)
            if not codes:
                (tmp_path / "data" / "stop.flag").write_text("stop")
            return code

    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *a, **k: Child())
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    assert desktop.serve(tmp_path, max_runs=10) == 3
    log = next((tmp_path / "data" / "logs").glob("*.log")).read_text(encoding="utf-8")
    assert "qayta ishga tushirildi" in log and "kod 1" in log
    assert not (tmp_path / "data" / "keeper.pid").exists()


@pytest.fixture
async def client(tmp_path):
    e = await build_env(make_settings(tmp_path, api_token="t0ken"))
    app = create_api(e.settings, e.sf)
    app.state.env_path = tmp_path / ".env"
    (tmp_path / ".env").write_text("SALES_BOT_TOKEN=111:secret\nSALES_SHOP_NAME=Nam Tour\n", encoding="utf-8")
    yield TestClient(app), tmp_path / ".env"
    await e.engine.dispose()


async def test_setup_needs_the_panel_password(client):
    c, _ = client
    assert c.get("/setup").status_code == 200
    assert c.get("/api/setup").status_code == 401
    assert c.post("/api/setup", json={"values": {}}).status_code == 401


async def test_setup_never_shows_secrets(client):
    c, _ = client
    data = c.get("/api/setup", headers={"X-API-Token": "t0ken"}).json()
    assert "111:secret" not in json.dumps(data)
    bot = next(f for f in data["fields"] if f["key"] == "SALES_BOT_TOKEN")
    assert bot["set"] is True and "value" not in bot
    assert data["channels"]["telegram"] is True


async def test_setup_saves_validates_and_keeps_empty_secrets(client):
    c, path = client
    h = {"X-API-Token": "t0ken"}
    bad = c.post("/api/setup", headers=h, json={"values": {"SALES_MANAGER_IDS": "Jasur"}})
    assert bad.status_code == 400 and "SALES_MANAGER_IDS" in bad.json()["detail"]
    assert c.post("/api/setup", headers=h, json={"values": {"PATH": "x"}}).status_code == 400
    assert c.post("/api/setup", headers=h, json={"values": {"SALES_PAYMENT_PROVIDER": "bitcoin"}}).status_code == 400
    r = c.post("/api/setup", headers=h, json={"values": {"SALES_BOT_TOKEN": "", "SALES_MANAGER_IDS": "111, 222",
                                                        "SALES_PAYMENT_CARD": "8600 1234 5678 9012 (Jasur N.)"}})
    assert r.status_code == 200 and r.json()["saved"] == ["SALES_MANAGER_IDS", "SALES_PAYMENT_CARD"] and r.json()["restarting"] is False
    stored = envfile.read(path)
    assert stored["SALES_BOT_TOKEN"] == "111:secret" and stored["SALES_PAYMENT_CARD"] == "8600 1234 5678 9012 (Jasur N.)"
    assert Settings(_env_file=str(path)).sales_manager_ids == [111, 222]


def test_bad_bot_token_does_not_stop_the_agent():
    assert app_main._make_bot("not-a-token", "test") is None
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("no internet")

    asyncio.run(app_main._keep_polling("test", flaky, retry=0))
    assert len(calls) == 3
