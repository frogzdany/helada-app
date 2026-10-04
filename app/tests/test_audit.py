import sqlite3

import pytest

from backend.audit import GENESIS, AuditLog, canonical_json, entry_hash
from backend.db import DB


@pytest.fixture
def log(tmp_path):
    db = DB(tmp_path / "a.db")
    a = AuditLog(db)
    for i in range(5):
        a.append("tester", f"act.{i}", f"e:{i}", {"i": i, "texto": "helada ñ"})
    return a


def test_chain_verifies(log):
    v = log.verify()
    assert v["ok"] and v["n"] == 5
    rows = log.db.all("SELECT * FROM audit ORDER BY seq")
    assert rows[0]["prev_hash"] == GENESIS
    for a, b in zip(rows, rows[1:]):
        assert b["prev_hash"] == a["hash"]
    r = rows[2]
    entry = {k: r[k] for k in ("seq", "ts", "actor", "action", "entity", "payload")}
    assert entry_hash(r["prev_hash"], entry) == r["hash"]


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": "ñ"}) == canonical_json({"a": "ñ", "b": 1}) == '{"a":"ñ","b":1}'


def test_update_and_delete_are_blocked(log):
    with pytest.raises(sqlite3.DatabaseError):
        log.db.execute("UPDATE audit SET actor='x' WHERE seq=2")
    with pytest.raises(sqlite3.DatabaseError):
        log.db.execute("DELETE FROM audit WHERE seq=5")
    assert log.verify()["ok"]


def test_tamper_detected_when_triggers_bypassed(log):
    log.db.execute("DROP TRIGGER audit_no_update")
    log.db.execute("""UPDATE audit SET payload='{"i":99}' WHERE seq=3""")
    v = log.verify()
    assert not v["ok"] and v["broken_at"] == 3 and v["why"] == "hash mismatch"


def test_rehash_attack_breaks_next_link(log):
    """An attacker who edits row 3 AND recomputes its hash still breaks row 4's prev_hash."""
    log.db.execute("DROP TRIGGER audit_no_update")
    r = log.db.one("SELECT * FROM audit WHERE seq=3")
    r["payload"] = '{"i":99}'
    new = entry_hash(r["prev_hash"], {k: r[k] for k in ("seq", "ts", "actor", "action", "entity", "payload")})
    log.db.execute("UPDATE audit SET payload=?, hash=? WHERE seq=3", (r["payload"], new))
    v = log.verify()
    assert not v["ok"] and v["broken_at"] == 4 and v["why"] == "prev_hash mismatch"


def test_deletion_detected(log):
    log.db.execute("DROP TRIGGER audit_no_delete")
    log.db.execute("DELETE FROM audit WHERE seq=2")
    assert not log.verify()["ok"]
