"""The phone page (static/movil) runs the frost model in JavaScript. These tests keep it honest:
the pack on disk matches the model artifacts, and the JS code reproduces the Python model's outputs."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
MOVIL = APP / "static" / "movil"


def test_pack_models_are_the_python_artifacts():
    from helada_model import core
    for pack_file, sub in (("pack.json", ""), ("pack-demo.json", "holdout_2025_26")):
        pack = json.loads((MOVIL / "data" / pack_file).read_text(encoding="utf-8"))
        total = 0
        for fn in pack["models"]["files"].values():
            shipped = (MOVIL / pack["models"]["dir"] / fn).read_bytes()
            assert shipped == (Path(core.ART) / sub / fn).read_bytes(), f"{pack_file}: {fn} is stale; run scripts/build_movil_pack.py"
            total += len(shipped)
        assert total == pack["models"]["total_bytes"] < 1_000_000   # "small enough to side-load": under 1 MB


def test_pack_has_a_person_to_ask_and_a_fixed_list_of_answers():
    pack = json.loads((MOVIL / "data" / "pack.json").read_text(encoding="utf-8"))
    assert pack["contact"]["name"] and pack["contact"]["phone"]
    assert pack["failsafe"]["max_lead_days"] >= pack["failsafe"]["fresh_lead_days"] >= 0
    for crop in pack["advisory"]["crops"].values():
        for stage in crop["stages"]:
            assert stage["actions"] and all(a["text"] for a in stage["actions"])


def test_service_worker_caches_every_file_the_page_needs():
    sw = (MOVIL / "sw.js").read_text(encoding="utf-8")
    for rel in ("index.html", "movil.css", "movil.js", "helada-model.js", "i18n.js", "data/pack.json", "data/pack-demo.json"):
        assert f'"{rel}"' in sw
    for pack_file in ("pack.json", "pack-demo.json"):
        pack = json.loads((MOVIL / "data" / pack_file).read_text(encoding="utf-8"))
        for fn in pack["models"]["files"].values():
            assert f'"{pack["models"]["dir"]}{fn}"' in sw


def test_english_reading_aid_follows_the_spanish_advisory_table():
    """The English switch is for reviewers. It must mirror the Spanish table (the one an agronomist signs):
    same crops, same stages, same number of actions, nothing empty."""
    for pack_file in ("pack.json", "pack-demo.json"):
        adv = json.loads((MOVIL / "data" / pack_file).read_text(encoding="utf-8"))["advisory"]
        assert set(adv["en"]["crops"]) == set(adv["crops"])
        for name, crop in adv["crops"].items():
            en = adv["en"]["crops"][name]
            assert len(en["stages"]) == len(crop["stages"]), name
            for es_stage, en_stage in zip(crop["stages"], en["stages"]):
                assert en_stage["stage"].strip()
                assert len(en_stage["actions"]) == len(es_stage["actions"]), (name, es_stage["stage"])
                assert all(isinstance(a, str) and a.strip() for a in en_stage["actions"])


def test_every_static_text_in_the_page_has_a_key():
    import re
    html = (MOVIL / "index.html").read_text(encoding="utf-8")
    i18n = (MOVIL / "i18n.js").read_text(encoding="utf-8")
    for key in set(re.findall(r'data-t(?:-ph)?="([a-z0-9_]+)"', html)):
        assert i18n.count(f"    {key}: ") == 2, f"{key} must be in both the es and the en table"


def test_service_worker_version_is_the_hash_of_the_shell():
    """A phone must never show a mix of old and new files. The service worker serves one cache per VERSION, so
    every change to a shell file needs a new VERSION: run `scripts/build_movil_pack.py --stamp`."""
    import re
    import sys
    sys.path.insert(0, str(APP / "scripts"))
    import build_movil_pack
    sw = (MOVIL / "sw.js").read_text(encoding="utf-8")
    assert re.search(r'const VERSION = "([^"]+)";', sw).group(1) == build_movil_pack.shell_version(), \
        "static/movil changed: run `uv run python scripts/build_movil_pack.py --stamp`"
    assert "cache.put(" not in sw, "no per-file refresh: files are only replaced as a whole set"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_model_matches_python_model():
    r = subprocess.run(["node", str(APP / "tests" / "movil_parity.mjs")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failures" in r.stdout
