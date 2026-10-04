"""Durable state on AWS: rows in DynamoDB, media files in S3. Entry point: ``backend.cloud:app``.

The app keeps working on its local SQLite file and data folder (the same code that runs on a laptop or a
Raspberry Pi). This module makes that state durable when the app runs on Lambda, where the local disk is
lost with the instance:

  - at start, before the app is created, every row is loaded from the DynamoDB table into SQLite and every
    media file is copied back from the bucket;
  - while running, SQLite triggers note each inserted, updated or deleted row; before a response is sent,
    those rows are written to DynamoDB and new or removed media files are synced to the bucket.

DynamoDB is the system of record and SQLite is the working copy of one instance, so the function must run as
a single instance (the stack reserves a concurrency of 1). Without ``HELADA_DDB_TABLE`` this module is just
the plain app, and nothing here is used.

Table layout (one table): ``pk`` = SQLite table name, ``sk`` = the row's primary key (integers zero-padded so
they sort), ``row`` = the columns.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Protocol

log = logging.getLogger("helada.cloud")

CHANGES = "_cloud_changes"
MAX_ITEM_BYTES = 350_000            # DynamoDB's item limit is 400 KB
SYNCED_DIRS = ("media", "packets")  # under the data dir
SKIP_SUFFIXES = (".16k.wav",)       # transient speech-to-text input


# ------------------------------------------------------------------ storage interfaces
class Rows(Protocol):
    def load(self, table: str) -> Iterable[dict]: ...
    def write(self, puts: list[dict], deletes: list[tuple[str, str]]) -> None: ...


class Files(Protocol):
    def keys(self) -> Iterable[str]: ...
    def download(self, key: str, path: Path) -> None: ...
    def upload(self, path: Path, key: str) -> None: ...
    def delete(self, key: str) -> None: ...


class DynamoRows:
    """One DynamoDB table, partition key ``pk`` (string), sort key ``sk`` (string)."""

    def __init__(self, table_name: str):
        import boto3
        from boto3.dynamodb.conditions import Key
        self._key = Key
        self.table = boto3.resource("dynamodb").Table(table_name)

    def load(self, table: str) -> Iterable[dict]:
        kwargs: dict = {"KeyConditionExpression": self._key("pk").eq(table), "ConsistentRead": True}
        while True:
            page = self.table.query(**kwargs)
            yield from (i["row"] for i in page["Items"])
            if "LastEvaluatedKey" not in page:
                return
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def write(self, puts: list[dict], deletes: list[tuple[str, str]]) -> None:
        with self.table.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as b:
            for pk, sk in deletes:
                b.delete_item(Key={"pk": pk, "sk": sk})
            for item in puts:
                b.put_item(Item=item)


class S3Files:
    def __init__(self, bucket: str, prefix: str = "data"):
        import boto3
        self.s3 = boto3.client("s3")
        self.bucket, self.prefix = bucket, prefix.strip("/")

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}"

    def keys(self) -> Iterable[str]:
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix + "/"):
            for o in page.get("Contents", []):
                yield o["Key"][len(self.prefix) + 1:]

    def download(self, key: str, path: Path) -> None:
        self.s3.download_file(self.bucket, self._k(key), str(path))

    def upload(self, path: Path, key: str) -> None:
        self.s3.upload_file(str(path), self.bucket, self._k(key))

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self._k(key))


# ------------------------------------------------------------------ the mirror
def _sk(value: Any) -> str:
    return f"{value:012d}" if isinstance(value, int) else str(value)


def _to_item_value(v: Any) -> Any:
    return Decimal(repr(v)) if isinstance(v, float) else v


def _from_item_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


class CloudStore:
    """Keeps one SQLite file and its data folder in step with a ``Rows`` store and a ``Files`` store."""

    def __init__(self, db_path: Path, data_dir: Path, rows: Rows, files: Files | None = None):
        self.db_path, self.data_dir, self.rows, self.files = Path(db_path), Path(data_dir), rows, files
        self.lock = threading.Lock()
        self._known: dict[str, tuple[int, int]] = {}   # file key -> (size, mtime_ns) as last synced
        self.stats = {"restored_rows": 0, "restored_files": 0, "rows_written": 0, "rows_deleted": 0,
                      "files_uploaded": 0, "files_deleted": 0, "last_flush": None, "last_error": None}

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    # ---- schema helpers
    @staticmethod
    def _user_tables(c: sqlite3.Connection) -> dict[str, tuple[str, list[str]]]:
        """table -> (primary key column, all columns), for tables with a single-column primary key."""
        out = {}
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if name.startswith(("sqlite_", "_cloud_")):
                continue
            info = c.execute(f"PRAGMA table_info({name})").fetchall()
            pks = [r["name"] for r in info if r["pk"]]
            if len(pks) == 1:
                out[name] = (pks[0], [r["name"] for r in info])
        return out

    def _track(self, c: sqlite3.Connection) -> None:
        """Triggers that note every changed row. A table seen for the first time has all its rows noted, so
        rows written before its triggers existed (a table the app creates lazily) are not missed."""
        c.execute(f"CREATE TABLE IF NOT EXISTS {CHANGES} (n INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "tbl TEXT NOT NULL, pk TEXT NOT NULL, op TEXT NOT NULL)")
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
        for t, (pk, _) in self._user_tables(c).items():
            if f"_cloud_{t}_ai" in have:
                continue
            for suffix, event, ref, op in (("ai", "INSERT", "NEW", "put"), ("au", "UPDATE", "NEW", "put"),
                                           ("ad", "DELETE", "OLD", "del")):
                c.execute(f"CREATE TRIGGER _cloud_{t}_{suffix} AFTER {event} ON {t} BEGIN "
                          f"INSERT INTO {CHANGES}(tbl, pk, op) VALUES ('{t}', {ref}.{pk}, '{op}'); END")
            c.execute(f"INSERT INTO {CHANGES}(tbl, pk, op) SELECT '{t}', {pk}, 'put' FROM {t}")

    # ---- restore (once, at start, before the app touches the database)
    def restore(self) -> None:
        with self.lock, self._conn() as c:
            n = 0
            for t, (_, columns) in self._user_tables(c).items():
                cols = set(columns)
                for row in self.rows.load(t):
                    keep = {k: _from_item_value(v) for k, v in row.items() if k in cols}
                    c.execute(f"INSERT OR REPLACE INTO {t} ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})",
                              tuple(keep.values()))
                    n += 1
            self.stats["restored_rows"] = n
            self._track(c)
            c.execute(f"DELETE FROM {CHANGES}")   # what was just loaded is not a change
        if self.files:
            keys = [k for k in self.files.keys() if k.split("/")[0] in SYNCED_DIRS]

            def fetch(key: str) -> None:
                path = self.data_dir / key
                path.parent.mkdir(parents=True, exist_ok=True)
                self.files.download(key, path)

            with ThreadPoolExecutor(max_workers=16) as pool:
                list(pool.map(fetch, keys))
            self._known = self._scan()
            self.stats["restored_files"] = len(keys)
        log.info("cloud: restored %s rows and %s files", self.stats["restored_rows"], self.stats["restored_files"])

    # ---- flush (before each response)
    def _scan(self) -> dict[str, tuple[int, int]]:
        out = {}
        for d in SYNCED_DIRS:
            root = self.data_dir / d
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                if p.is_file() and not p.name.endswith(SKIP_SUFFIXES) and not p.name.startswith("."):
                    st = p.stat()
                    out[p.relative_to(self.data_dir).as_posix()] = (st.st_size, st.st_mtime_ns)
        return out

    def flush(self) -> None:
        with self.lock:
            with self._conn() as c:
                self._track(c)
                changes = c.execute(f"SELECT n, tbl, pk FROM {CHANGES} ORDER BY n").fetchall()
                if changes:
                    tables = self._user_tables(c)
                    last = changes[-1]["n"]
                    puts, deletes = [], []
                    for tbl, pk in dict.fromkeys((r["tbl"], r["pk"]) for r in changes):   # each row once
                        if tbl not in tables:
                            continue
                        pk_col = tables[tbl][0]
                        row = c.execute(f"SELECT * FROM {tbl} WHERE CAST({pk_col} AS TEXT) = ?", (pk,)).fetchone()
                        if row is None:   # gone by now, whatever happened in between
                            sk = _sk(int(pk)) if pk.lstrip("-").isdigit() and self._int_pk(c, tbl, pk_col) else pk
                            deletes.append((tbl, sk))
                            continue
                        item = {"pk": tbl, "sk": _sk(row[pk_col]),
                                "row": {k: _to_item_value(row[k]) for k in row.keys() if row[k] is not None}}
                        if sum(len(str(v)) for v in item["row"].values()) > MAX_ITEM_BYTES:
                            log.warning("cloud: %s %s is too large for one DynamoDB item; not stored", tbl, pk)
                            continue
                        puts.append(item)
                    self.rows.write(puts, deletes)
                    c.execute(f"DELETE FROM {CHANGES} WHERE n <= ?", (last,))
                    self.stats["rows_written"] += len(puts)
                    self.stats["rows_deleted"] += len(deletes)
            if self.files:
                now = self._scan()
                for key, sig in now.items():
                    if self._known.get(key) != sig:
                        self.files.upload(self.data_dir / key, key)
                        self.stats["files_uploaded"] += 1
                for key in self._known.keys() - now.keys():
                    self.files.delete(key)
                    self.stats["files_deleted"] += 1
                self._known = now
            self.stats["last_flush"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.stats["last_error"] = None

    @staticmethod
    def _int_pk(c: sqlite3.Connection, tbl: str, pk_col: str) -> bool:
        info = c.execute(f"PRAGMA table_info({tbl})").fetchall()
        return any(r["name"] == pk_col and "INT" in (r["type"] or "").upper() for r in info)

    def flush_safely(self) -> None:
        """A failed write must not fail the farmer's request: the changes stay noted and go out with the next one."""
        try:
            self.flush()
        except Exception as e:   # noqa: BLE001
            self.stats["last_error"] = f"{type(e).__name__}: {e}"
            log.exception("cloud: flush failed; changes are kept for the next request")


# ------------------------------------------------------------------ ASGI wrapper
class Persist:
    """Flushes the store before the last byte of every response, because Lambda freezes the process right after."""

    STATUS_PATH = "/api/storage"

    def __init__(self, app, store: CloudStore, describe: dict):
        self.app, self.store, self.describe = app, store, describe

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == self.STATUS_PATH and scope["method"] == "GET":
            import json
            body = json.dumps({**self.describe, **self.store.stats}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
            return await send({"type": "http.response.body", "body": body})

        async def send_after_flush(message):
            if message["type"] == "http.response.body" and not message.get("more_body"):
                await asyncio.to_thread(self.store.flush_safely)
            await send(message)

        await self.app(scope, receive, send_after_flush)


def wrap(settings, rows: Rows, files: Files | None, make_app, describe: dict | None = None) -> Persist:
    """Create the schema, load the durable state into it, then create the app on top and wrap it."""
    from . import parcel_logger
    from .db import DB
    settings.ensure_dirs()
    db = DB(settings.db_path)
    parcel_logger.ensure_schema(db)    # created lazily by the app; needed before its rows can be loaded
    store = CloudStore(settings.db_path, settings.data_dir, rows, files)
    store.restore()                    # raises if the store cannot be read: better no instance than a blank one
    return Persist(make_app(), store, describe or {})


def _main_app():
    # Imported only now: importing backend.main creates the app, which must find the restored database.
    from .main import app as plain
    return plain


def build():
    table = os.environ.get("HELADA_DDB_TABLE", "")
    if not table:
        return _main_app()
    from .config import Settings
    bucket = os.environ.get("HELADA_MEDIA_BUCKET", "")
    return wrap(Settings(), DynamoRows(table), S3Files(bucket) if bucket else None, _main_app,
                {"rows": f"dynamodb:{table}", "files": f"s3:{bucket}" if bucket else None})


app = build()
