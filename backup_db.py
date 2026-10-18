"""Create a consistent SQLite backup of the configured TrueDose database.

Usage: python backup_db.py [optional-output-path]
"""
import os
import sys
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source_path = Path(os.environ.get("TRUEDOSE_DB_PATH", str(ROOT / "truedose.db"))).expanduser().resolve()
if not source_path.exists():
    raise SystemExit(f"Database not found at {source_path}. Start TrueDose AI once or set TRUEDOSE_DB_PATH.")
if len(sys.argv) > 1:
    target_path = Path(sys.argv[1]).expanduser().resolve()
else:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_path = source_path.with_name(f"{source_path.stem}-backup-{stamp}{source_path.suffix or '.db'}")
target_path.parent.mkdir(parents=True, exist_ok=True)
if source_path == target_path:
    raise SystemExit("Backup destination must differ from the source database.")
with sqlite3.connect(source_path) as source, sqlite3.connect(target_path) as target:
    source.backup(target)
with sqlite3.connect(target_path) as check:
    result = check.execute("PRAGMA integrity_check").fetchone()[0]
if result != "ok":
    target_path.unlink(missing_ok=True)
    raise SystemExit(f"Backup integrity check failed: {result}")
print(f"Backup created and verified: {target_path}")
