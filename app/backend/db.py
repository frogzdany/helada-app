"""SQLite storage. Plain sqlite3, one connection per call site, WAL mode."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS parcels (
  parcel_id TEXT PRIMARY KEY,
  phone TEXT,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
  phone TEXT PRIMARY KEY,
  opted_out INTEGER NOT NULL DEFAULT 0,
  last_inbound_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parcel_id TEXT NOT NULL,
  night_date TEXT NOT NULL,
  status TEXT NOT NULL,            -- sent | queued | failed | expired | cancelled (opted out)
  reason TEXT,
  text TEXT,
  sms_text TEXT,
  audio_path TEXT,
  channel TEXT,
  provider_id TEXT,
  forecast TEXT,
  created_at TEXT NOT NULL,
  sent_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_parcel ON alerts(parcel_id, created_at);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  parcel_id TEXT,
  direction TEXT NOT NULL,         -- in | out
  kind TEXT NOT NULL,              -- text | audio | image | document | location
  text TEXT,
  media_path TEXT,
  meta TEXT,
  channel TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_phone ON messages(phone, id);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parcel_id TEXT NOT NULL,
  phone TEXT NOT NULL,
  status TEXT NOT NULL,            -- open | packet | cancelled
  state TEXT NOT NULL,             -- json: slots, photos, location, asked, preregistro, engines, transcript
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packets (
  id TEXT PRIMARY KEY,
  parcel_id TEXT NOT NULL,
  report_id INTEGER,
  year INTEGER NOT NULL,
  pdf_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv_cache (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  entity TEXT,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(alerts)")}
            if "level" not in cols:   # migration: alert | watch (watches do not count toward the weekly cap)
                c.execute("ALTER TABLE alerts ADD COLUMN level TEXT NOT NULL DEFAULT 'alert'")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
        finally:
            c.close()

    # ---- tiny helpers ----
    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self.lock, self.conn() as c:
            cur = c.execute(sql, params)
            return cur.lastrowid

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self.conn() as c:
            r = c.execute(sql, params).fetchone()
            return dict(r) if r else None

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    # ---- cache ----
    def cache_get(self, key: str) -> Any | None:
        r = self.one("SELECT value FROM kv_cache WHERE key=?", (key,))
        return json.loads(r["value"]) if r else None

    def cache_put(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT OR REPLACE INTO kv_cache(key, value, fetched_at) VALUES (?,?,?)",
            (key, json.dumps(value, ensure_ascii=False), utcnow()),
        )
