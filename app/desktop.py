"""Desktop launcher for the installed Windows program (also works on Linux and macOS).

    pythonw -m app.desktop open    # the "Nam Tour Agent" shortcut: start the agent if needed, open the browser
    pythonw -m app.desktop serve   # keep the agent running (Startup folder); restarts it when it stops
    pythonw -m app.desktop stop    # stop the agent and its keeper

On the first start it creates .env from .env.example with a random panel
password (API_TOKEN), a random file-encryption key and local-only settings, so
the client never edits .env by hand: everything else is filled in on /setup.
"""
from __future__ import annotations

import base64
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from app import envfile

ROOT = Path(__file__).resolve().parents[1]
PORT = 8000
RESTART_CODE = 3            # the setup page exits with this code: start again at once with the new settings

FIRST_RUN = {               # written only when the key is missing or empty
    "API_HOST": "127.0.0.1",                                   # the panel is reachable from this computer only
    "DATABASE_URL": "sqlite+aiosqlite:///./data/namtour.db",
    "SALES_ENABLED": "true",
    "SALES_SHOP_NAME": "Jihan Jan Jasur Nam Tour",
    "SALES_CATALOG_PATH": "sales_catalog.json",
    "SALES_CATALOG_FROM_BOOKS": "false",
}


def _paths(root: Path) -> dict[str, Path]:
    data = root / "data"
    return {"env": root / ".env", "data": data, "logs": data / "logs", "keeper": data / "keeper.pid",
            "agent": data / "agent.pid", "stop": data / "stop.flag"}


def ensure_env(root: Path = ROOT) -> dict[str, str]:
    """Create or complete .env; never overwrite a value the client already set."""
    p = _paths(root)
    if not p["env"].exists():
        example = root / ".env.example"
        p["env"].write_text(example.read_text(encoding="utf-8-sig") if example.exists() else "", encoding="utf-8")
    current = envfile.read(p["env"])
    fill = {k: v for k, v in FIRST_RUN.items() if not current.get(k)}
    if not current.get("API_TOKEN"):
        fill["API_TOKEN"] = secrets.token_urlsafe(24)
    if not current.get("SALES_FILES_KEY"):
        fill["SALES_FILES_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode()   # a Fernet key
    if fill:
        envfile.update(p["env"], fill)
    return envfile.read(p["env"])


def configured(env: dict[str, str]) -> bool:
    """At least one channel the clients can write to is connected."""
    return any(env.get(k) for k in ("SALES_BOT_TOKEN", "WHATSAPP_ACCESS_TOKEN", "INSTAGRAM_ACCESS_TOKEN", "MESSENGER_PAGE_TOKEN"))


def port(env: dict[str, str]) -> int:
    try:
        return int(env.get("API_PORT") or PORT)
    except ValueError:
        return PORT


def healthy(p: int, timeout: float = 2.0) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never through a proxy: it is this computer
    try:
        with opener.open(f"http://127.0.0.1:{p}/health", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259                                           # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def _detached() -> dict:
    if os.name == "nt":
        return {"creationflags": 0x00000008 | 0x00000200 | 0x08000000}   # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    return {"start_new_session": True}


def _log(root: Path, text: str) -> None:
    logs = _paths(root)["logs"]
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / f"agent_{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%dT%H:%M:%S} {text}\n")


def serve(root: Path = ROOT, *, max_runs: int | None = None, delay: float = 10.0) -> int:
    """Keep `python -m app.main` running until `stop`. Returns the number of runs (for tests)."""
    p = _paths(root)
    ensure_env(root)
    p["logs"].mkdir(parents=True, exist_ok=True)
    if _alive(_read_pid(p["keeper"])) and _read_pid(p["keeper"]) != os.getpid():
        return 0                                                    # another keeper is already running
    p["keeper"].write_text(str(os.getpid()))
    p["stop"].unlink(missing_ok=True)
    env = {**os.environ, "NAMTOUR_SUPERVISED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    runs = 0
    try:
        while max_runs is None or runs < max_runs:
            runs += 1
            with open(p["logs"] / f"agent_{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as log:
                child = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL)
                p["agent"].write_text(str(child.pid))
                code = child.wait()
            if p["stop"].exists():
                break
            if code == RESTART_CODE:
                _log(root, "agent sozlamalar o‘zgargani uchun qayta ishga tushirildi")
                continue
            _log(root, f"agent to‘xtadi (kod {code}), {delay:.0f} soniyadan keyin qayta yoqiladi")
            time.sleep(delay)
    finally:
        p["agent"].unlink(missing_ok=True)
        if _read_pid(p["keeper"]) == os.getpid():
            p["keeper"].unlink(missing_ok=True)
    return runs


def stop(root: Path = ROOT) -> bool:
    p = _paths(root)
    p["data"].mkdir(parents=True, exist_ok=True)
    p["stop"].write_text("stop")
    stopped = False
    for key in ("agent", "keeper"):
        pid = _read_pid(p[key])
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                stopped = True
            except OSError:
                pass
    return stopped


def start_background(root: Path = ROOT) -> None:
    subprocess.Popen([sys.executable, "-m", "app.desktop", "serve"], cwd=root, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_detached())


def _alert(text: str) -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "Nam Tour Agent", 0x30)
    else:
        print(text, file=sys.stderr)


def open_app(root: Path = ROOT, *, wait: float = 90.0, browser=webbrowser.open) -> str | None:
    env = ensure_env(root)
    p = port(env)
    if not healthy(p):
        start_background(root)
        deadline = time.monotonic() + wait
        while not healthy(p):
            if time.monotonic() > deadline:
                _alert("Agent ishga tushmadi. Jurnal: " + str(_paths(root)["logs"]))
                return None
            time.sleep(1)
    url = f"http://127.0.0.1:{p}/{'panel' if configured(env) else 'setup'}#token={env['API_TOKEN']}"
    browser(url)
    return url


def main(argv: list[str] | None = None) -> None:
    os.chdir(ROOT)                      # .env, data/ and the catalog are found relative to the install folder
    cmd = (argv if argv is not None else sys.argv[1:] or ["open"])[0]
    if cmd == "serve":
        serve()
    elif cmd == "stop":
        stop()
    elif cmd == "open":
        open_app()
    else:
        raise SystemExit("usage: python -m app.desktop [open|serve|stop]")


if __name__ == "__main__":
    main()
