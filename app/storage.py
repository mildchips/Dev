"""Local-only SQLite persistence, explicit store partitioning."""
from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS stores (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, platform TEXT NOT NULL,
 is_demo INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS imports (
 id TEXT PRIMARY KEY, store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 filename TEXT NOT NULL, sha256 TEXT NOT NULL, count_rows INTEGER NOT NULL,
 control_kopeks INTEGER, imported_sum_kopeks INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(store_id,sha256)
);
CREATE TABLE IF NOT EXISTS ledger (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 import_id TEXT NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
 source_row INTEGER NOT NULL, date TEXT NOT NULL, sku TEXT NOT NULL,
 category TEXT NOT NULL, amount_kopeks INTEGER NOT NULL,
 quantity INTEGER, operation_id TEXT NOT NULL DEFAULT '', order_id TEXT NOT NULL DEFAULT '',
 description TEXT NOT NULL DEFAULT '', UNIQUE(import_id,source_row)
);
CREATE INDEX IF NOT EXISTS ledger_store_date ON ledger(store_id,date);
CREATE INDEX IF NOT EXISTS ledger_store_sku ON ledger(store_id,sku);
CREATE TABLE IF NOT EXISTS costs (
 store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 sku TEXT NOT NULL, unit_cost_kopeks INTEGER NOT NULL,
 modified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(store_id,sku)
);
CREATE TABLE IF NOT EXISTS local_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 action TEXT NOT NULL,
 store_id TEXT,
 subject TEXT,
 detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS local_audit_store ON local_audit(store_id,id);
CREATE TABLE IF NOT EXISTS action_plans (
 id TEXT PRIMARY KEY,
 store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 hypothesis TEXT NOT NULL,
 source_signal_key TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','planned','implemented','rejected')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS action_store ON action_plans(store_id,created_at);
CREATE TABLE IF NOT EXISTS cost_revisions (
 store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 sku TEXT NOT NULL,
 effective_from TEXT NOT NULL,
 unit_cost_kopeks INTEGER NOT NULL CHECK(unit_cost_kopeks >= 0),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(store_id,sku,effective_from)
);
CREATE TABLE IF NOT EXISTS reviews (
 store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 signal_key TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(store_id,signal_key)
);
"""

def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'posix':
        path.parent.chmod(0o700)
    with connect(path) as db:
        db.executescript(SCHEMA)
    if os.name == 'posix':
        path.chmod(0o600)

@contextmanager
def connect(path: Path):
    db = sqlite3.connect(str(path), timeout=10)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def log_action(db, action:str,store_id:str|None,subject:str='',detail:str=''):
    """No credentials or user data in metadata; commit with business transaction."""
    db.execute('INSERT INTO local_audit(action,store_id,subject,detail) VALUES(?,?,?,?)',
               (action[:60],store_id,(subject or '')[:120],(detail or '')[:200]))
