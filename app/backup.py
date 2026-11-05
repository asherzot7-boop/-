"""Backup of the database and client documents into one zip file.

    python -m app.main backup                     # into BACKUP_DIR (default data/backups)
    python -m app.main backup --to "G:\\My Drive\\NamTour"

Point --to at a Google Drive for desktop folder and the zip is in the cloud
too. SQLite is copied with its online backup API, so the copy is consistent
while the agent keeps running. The newest BACKUP_KEEP archives are kept.
"""
from __future__ import annotations

import logging
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

PREFIX = "agent_backup_"


def _sqlite_path(url: str) -> Path | None:
    if not url.startswith("sqlite"):
        return None
    path = url.split("///", 1)[-1]
    return None if not path or path == ":memory:" else Path(path)


def make_backup(settings, dest: str | Path | None = None, keep: int | None = None, now: datetime | None = None) -> Path:
    dest = Path(dest or settings.backup_dir)
    keep = keep if keep is not None else settings.backup_keep
    dest.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    target = dest / f"{PREFIX}{stamp}.zip"
    tmp_target = target.with_suffix(".zip.part")
    db = _sqlite_path(settings.database_url)
    files_dir = Path(settings.sales_files_dir)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(tmp_target, "w", zipfile.ZIP_DEFLATED) as z:
        if db is not None and db.exists():
            copy = Path(tmp) / "database.db"
            src, dst = sqlite3.connect(str(db)), sqlite3.connect(str(copy))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            z.write(copy, "database.db")
        elif db is None:
            z.writestr("README.txt", "PostgreSQL: ma'lumotlar bazasini pg_dump bilan alohida saqlang.\n")
        for name in (".env.example", "sales_catalog.json"):
            if Path(name).exists():
                z.write(name, name)
        if files_dir.exists():
            for f in sorted(files_dir.rglob("*")):
                if f.is_file():
                    z.write(f, "lead_files/" + f.relative_to(files_dir).as_posix())
    tmp_target.replace(target)  # a half-written archive never looks like a finished one
    old = sorted(dest.glob(f"{PREFIX}*.zip"))
    for f in old[:-keep] if keep > 0 else []:
        f.unlink(missing_ok=True)
    log.info("Захира нусха: %s", target)
    return target
