"""Append-only, hash-chained audit log.

hash_n = sha256( prev_hash_n  ||  canonical_json(entry_n) )
where prev_hash_0 = "0"*64, canonical_json = json.dumps(sort_keys=True,
separators=(",", ":"), ensure_ascii=False) encoded UTF-8, and entry_n is
{seq, ts, actor, action, entity, payload} (payload already canonical JSON).

Append-only is enforced twice: SQLite triggers reject UPDATE/DELETE, and any
edit that bypasses them breaks the chain (verify() reports the first bad seq).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .db import DB, utcnow

GENESIS = "0" * 64


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def entry_hash(prev_hash: str, entry: dict) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(canonical_json(entry).encode("utf-8"))
    return h.hexdigest()


def _entry(row: dict) -> dict:
    return {k: row[k] for k in ("seq", "ts", "actor", "action", "entity", "payload")}


class AuditLog:
    def __init__(self, db: DB):
        self.db = db

    def append(self, actor: str, action: str, entity: str | None, payload: dict) -> dict:
        with self.db.lock, self.db.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                last = c.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
                seq = (last["seq"] + 1) if last else 1
                prev = last["hash"] if last else GENESIS
                row = {"seq": seq, "ts": utcnow(), "actor": actor, "action": action,
                       "entity": entity, "payload": canonical_json(payload)}
                h = entry_hash(prev, _entry(row))
                c.execute(
                    "INSERT INTO audit(seq, ts, actor, action, entity, payload, prev_hash, hash) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (seq, row["ts"], actor, action, entity, row["payload"], prev, h),
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        return {**row, "prev_hash": prev, "hash": h}

    def entries(self, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM audit ORDER BY seq DESC"
        rows = self.db.all(sql + (f" LIMIT {int(limit)}" if limit else ""))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def head(self) -> str:
        r = self.db.one("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1")
        return r["hash"] if r else GENESIS

    def verify(self) -> dict:
        rows = self.db.all("SELECT * FROM audit ORDER BY seq ASC")
        prev = GENESIS
        for i, r in enumerate(rows, start=1):
            if r["seq"] != i:
                return {"ok": False, "n": len(rows), "broken_at": r["seq"], "why": "seq gap"}
            if r["prev_hash"] != prev:
                return {"ok": False, "n": len(rows), "broken_at": r["seq"], "why": "prev_hash mismatch"}
            if entry_hash(prev, _entry(r)) != r["hash"]:
                return {"ok": False, "n": len(rows), "broken_at": r["seq"], "why": "hash mismatch"}
            prev = r["hash"]
        return {"ok": True, "n": len(rows), "head": prev}
