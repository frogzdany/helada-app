"""backend.cloud: state written by one instance is there for the next one (rows and media files)."""
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import cloud
from backend.config import Settings
from backend.main import create_app


class FakeRows:
    """Stands in for the DynamoDB table: {(pk, sk): row}."""

    def __init__(self):
        self.items = {}
        self.fail = False

    def tables(self):
        return sorted({pk for pk, _ in self.items})

    def load(self, table):
        return [row for (pk, _), row in sorted(self.items.items()) if pk == table]

    def write(self, puts, deletes):
        if self.fail:
            raise RuntimeError("store unreachable")
        for key in deletes:
            self.items.pop(key, None)
        for item in puts:
            self.items[(item["pk"], item["sk"])] = item["row"]


class FakeFiles:
    """Stands in for the bucket: a folder."""

    def __init__(self, root: Path):
        self.root = root

    def keys(self):
        return [p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()]

    def download(self, key, path):
        shutil.copy(self.root / key, path)

    def upload(self, path, key):
        (self.root / key).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, self.root / key)

    def delete(self, key):
        (self.root / key).unlink()


@pytest.fixture
def remote(tmp_path):
    (tmp_path / "bucket").mkdir()
    return FakeRows(), FakeFiles(tmp_path / "bucket")


def instance(tmp_path, name, remote):
    """A fresh instance: empty local disk, same remote stores."""
    s = Settings(data_dir=tmp_path / name, offline=True, tts="off", asr="mock", llm_url="", scheduler=False,
                 channel="sim")
    return s, cloud.wrap(s, remote[0], remote[1], lambda: create_app(s))


def test_state_survives_a_new_instance(tmp_path, remote):
    rows, _ = remote
    _, app1 = instance(tmp_path, "a", remote)
    with TestClient(app1) as c:
        parcels = c.get("/api/parcels").json()["parcels"]
        phone = parcels[0]["phone"]
        assert c.post("/api/sim/inbound", data={"phone": phone, "text": "hola"}).status_code == 200
        sent = c.get("/api/sim/messages", params={"phone": phone}).json()
        audit1 = c.get("/api/audit").json()
    assert sent, "the conversation produced messages"
    assert {"parcels", "messages", "audit"} <= set(rows.tables())
    assert len(rows.load("parcels")) == len(parcels)

    _, app2 = instance(tmp_path, "b", remote)
    with TestClient(app2) as c:
        assert c.get("/api/sim/messages", params={"phone": phone}).json() == sent
        audit2 = c.get("/api/audit").json()
        # the hash chain is intact and continues: no second genesis entry, nothing rewritten
        assert audit2["verify"]["ok"] is True
        assert {e["hash"] for e in audit1["entries"]} <= {e["hash"] for e in audit2["entries"]}
        assert sum(e["action"] == "system.start" for e in audit2["entries"]) == 1
        # new ids continue after the restored ones
        c.post("/api/sim/inbound", data={"phone": phone, "text": "otra vez"})
        after = c.get("/api/sim/messages", params={"phone": phone}).json()
    assert len(after) > len(sent)
    assert [m["id"] for m in after] == sorted({m["id"] for m in after})


def test_deletes_reach_the_store(tmp_path, remote):
    rows, _ = remote
    _, app1 = instance(tmp_path, "a", remote)
    with TestClient(app1) as c:
        phone = c.get("/api/parcels").json()["parcels"][0]["phone"]
        c.post("/api/sim/inbound", data={"phone": phone, "text": "hola"})
        assert rows.load("messages")
        assert c.post("/api/demo/reset").status_code == 200
    assert rows.load("messages") == []
    assert rows.load("parcels"), "the roster is not part of a reset"

    _, app2 = instance(tmp_path, "b", remote)
    with TestClient(app2) as c:
        assert c.get("/api/sim/messages", params={"phone": phone}).json() == []


def test_media_files_survive(tmp_path, remote):
    _, files = remote
    s1, app1 = instance(tmp_path, "a", remote)
    with TestClient(app1) as c:
        (s1.media_dir / "in").mkdir(parents=True, exist_ok=True)
        (s1.media_dir / "in" / "nota.ogg").write_bytes(b"voz")
        (s1.media_dir / "in" / "nota.16k.wav").write_bytes(b"transient")
        c.get("/api/parcels")
        assert "media/in/nota.ogg" in files.keys()
        assert "media/in/nota.16k.wav" not in files.keys()

    s2, app2 = instance(tmp_path, "b", remote)
    assert (s2.media_dir / "in" / "nota.ogg").read_bytes() == b"voz"
    with TestClient(app2) as c:
        assert c.get("/files/media/in/nota.ogg").content == b"voz"
        (s2.media_dir / "in" / "nota.ogg").unlink()
        c.get("/api/parcels")
    assert "media/in/nota.ogg" not in files.keys()


def test_a_failed_write_is_retried_with_the_next_request(tmp_path, remote):
    rows, _ = remote
    _, app1 = instance(tmp_path, "a", remote)
    with TestClient(app1) as c:
        phone = c.get("/api/parcels").json()["parcels"][0]["phone"]
        rows.fail = True
        assert c.post("/api/sim/inbound", data={"phone": phone, "text": "hola"}).status_code == 200
        assert rows.load("messages") == []
        assert c.get("/api/storage").json()["last_error"].startswith("RuntimeError")
        rows.fail = False
        c.get("/api/parcels")
        assert rows.load("messages")
        assert c.get("/api/storage").json()["last_error"] is None


def test_numbers_keep_their_type(tmp_path, remote):
    rows, _ = remote
    _, app1 = instance(tmp_path, "a", remote)
    with TestClient(app1) as c:
        pid = c.get("/api/parcels").json()["parcels"][0]["parcel_id"]
        r = c.post(f"/api/logger/{pid}/readings", data={"text": "2026-01-10,-1.5\n2026-01-11,2", "date_is": "evening"})
        assert r.status_code == 200, r.text
    assert len(rows.load("logger_obs")) == 2

    s2, _ = instance(tmp_path, "b", remote)
    got = sqlite3.connect(s2.db_path).execute("SELECT id, tmin_c FROM logger_obs ORDER BY id").fetchall()
    assert [v for _, v in got] == [-1.5, 2.0]
    assert all(isinstance(i, int) and isinstance(v, float) for i, v in got)
