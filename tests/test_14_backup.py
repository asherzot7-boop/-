"""Кунлик захира нусха: база ва мижоз ҳужжатлари битта zip'да, эскилари ўчирилади."""
import sqlite3
import zipfile
from datetime import datetime, timedelta

from app.backup import make_backup
from tests.conftest import make_settings


def test_backup_contains_database_and_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "agent.db"
    with sqlite3.connect(db) as con:
        con.execute("create table leads (id integer, name text)")
        con.execute("insert into leads values (1, 'Bekzod')")
    files = tmp_path / "lead_files" / "1"
    files.mkdir(parents=True)
    (files / "ab12_pasport.pdf").write_bytes(b"%PDF")
    settings = make_settings(tmp_path, database_url=f"sqlite+aiosqlite:///{db}", sales_files_dir=str(tmp_path / "lead_files"))
    out = make_backup(settings, tmp_path / "drive", keep=3)
    with zipfile.ZipFile(out) as z:
        assert set(z.namelist()) == {"database.db", "lead_files/1/ab12_pasport.pdf"}
        z.extract("database.db", tmp_path / "check")
    with sqlite3.connect(tmp_path / "check" / "database.db") as con:
        assert con.execute("select name from leads").fetchone() == ("Bekzod",)


def test_backup_keeps_only_the_newest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = make_settings(tmp_path, database_url=f"sqlite+aiosqlite:///{tmp_path}/none.db", sales_files_dir=str(tmp_path / "x"))
    start = datetime(2026, 11, 1, 3, 0)
    for i in range(5):
        make_backup(settings, tmp_path / "b", keep=3, now=start + timedelta(days=i))
    names = sorted(p.name for p in (tmp_path / "b").iterdir())
    assert names == ["agent_backup_2026-11-03_030000.zip", "agent_backup_2026-11-04_030000.zip", "agent_backup_2026-11-05_030000.zip"]
