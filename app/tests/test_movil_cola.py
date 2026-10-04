"""The phone page's send queue (static/movil/cola.js): each tab counts only what waits on that tab, a refused
send is said as refused, and a host with no Helada server leaves nothing pending for ever."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
MOVIL = APP / "static" / "movil"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_queue_rules():
    r = subprocess.run(["node", str(APP / "tests" / "movil_cola.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failures" in r.stdout


def test_page_loads_the_queue_rules_and_the_service_worker_caches_them():
    html = (MOVIL / "index.html").read_text(encoding="utf-8")
    assert html.index('src="cola.js"') < html.index('src="movil.js"')
    assert '"cola.js"' in (MOVIL / "sw.js").read_text(encoding="utf-8")


def test_each_tab_counts_its_own_kind():
    js = (MOVIL / "movil.js").read_text(encoding="utf-8")
    assert 'badge("colaBadge", Q.pendientes(q, "dano", p.parcel_id))' in js
    assert re.search(r'badge\("termoBadge", .*Q\.pendientes\(q, "lectura", p\.parcel_id\)', js)


def test_row_states_have_words_in_both_languages():
    i18n = (MOVIL / "i18n.js").read_text(encoding="utf-8")
    for key in ("lect_sent", "lect_wait", "lect_saved", "dano_sent", "dano_wait", "send_failed", "only_here"):
        assert i18n.count(f"    {key}: ") == 2, f"{key} must be in both the es and the en table"
