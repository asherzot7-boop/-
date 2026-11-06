"""Read and update the .env file without losing comments, order or other keys.

Used by the setup page and the desktop launcher, so the client never has to
open .env in Notepad. Values are written the way pydantic-settings (python-dotenv)
reads them back: plain when safe, otherwise in double quotes with \\ and " escaped.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_PLAIN = re.compile(r"^[A-Za-z0-9_@%+=:,./\-]*$")


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return re.sub(r'\\(["\\])', r"\1", raw[1:-1])
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    return raw.split(" #", 1)[0].strip()


def quote(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("value must be a single line")
    if _PLAIN.match(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read(path: str | Path) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        m = _LINE.match(line)
        if m and not line.lstrip().startswith("#"):
            out[m.group(1)] = _unquote(m.group(2))
    return out


def update(path: str | Path, values: dict[str, str]) -> None:
    """Set KEY=value for each item: replaced in place where the key exists, appended otherwise.

    Written to a temporary file first and then swapped in, so a crash never leaves half a file.
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8-sig").splitlines() if p.exists() else []
    todo = dict(values)
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(1) in todo:
            key = m.group(1)
            lines[i] = f"{key}={quote(todo.pop(key))}"
    if todo:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"{k}={quote(v)}" for k, v in todo.items()]
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, p)
