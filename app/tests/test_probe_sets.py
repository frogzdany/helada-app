"""The message probe sets (evals/probes/*.json), run OFFLINE against the deterministic rules (intents.py,
slots.py). No model and no network: each case is a Spanish message, its context and the expected labels.

Sets: j1 intent (probe + a held-out set by a different author), j2 pre-registro, j3 cause/crop (+ the speech-benchmark transcripts),
j4 farmer-stated stage. Every (case, label) is one test. Known misses are listed in KNOWN_MISSES with the reason
and run as strict xfail, so nothing is skipped silently and a fixed miss turns the suite red until it is removed.

Print the score table:  cd app && uv run python tests/test_probe_sets.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from backend.intents import classify, read_preregistro, read_stage  # noqa: E402
from backend.slots import RegexSlotFiller  # noqa: E402

EV = APP.parent / "evals" / "probes"
TODAY = date(2026, 10, 4)
AREA = 2.5

J1_LABEL = {"reporta_dano": "damage", "pide_baja": "optout", "pregunta_pronostico": "forecast",
            "pregunta_siembra": "planting", "pregunta_programa": "program"}
PENDING = {"¿Más o menos cuánto terreno se dañó?": "area_ha", "¿Qué cultivo se dañó?": "crop",
           "¿Qué le pasó a su cultivo?": "cause", "¿Qué día pasó?": "date"}
PRE = {"si": "si_lo_hizo", "no": "no_lo_ha_hecho", "unclear": "no_queda_claro"}

# (set, case name, label) -> why the rules still miss it. Strict xfail.
KNOWN_MISSES = {
    ("bench_j3_causa_cultivo", "asr c08", "cultivo"):
        "ASR turned 'haba' into 'agua' ('daño el agua'): the crop is not in the text",
}


def _load(name: str) -> list[dict]:
    return json.loads((EV / f"{name}.json").read_text(encoding="utf-8"))["cases"]


def predict(set_name: str, case: dict) -> dict:
    st = case["state"]
    kind = set_name.replace("holdout_", "").replace("bench_", "")
    if kind.startswith("j1"):
        r = classify(st["mensaje"], pending=PENDING.get(st.get("pregunta_pendiente") or ""), today=TODAY,
                     parcel_area=AREA)
        return {q: ("yes" if r.labels[lab] else "no") for q, lab in J1_LABEL.items()}
    if kind.startswith("j2"):
        return {"prerregistro": PRE[read_preregistro(st["respuesta"])]}
    if kind.startswith("j3"):
        s = RegexSlotFiller().fill(st["mensaje"], TODAY, AREA)
        return {"causa": s["cause"] or "no_dicha", "cultivo": s["crop"] or "no_dicho"}
    if kind.startswith("j4"):
        return {"etapa": read_stage(st["mensaje"]) or "no_dicha"}
    raise ValueError(set_name)


SETS = ["j1_intencion", "holdout_j1_intencion", "j2_prerregistro", "holdout_j2_prerregistro",
        "j3_causa_cultivo", "holdout_j3_causa_cultivo", "bench_j3_causa_cultivo", "j4_etapa", "holdout_j4_etapa"]


def _params():
    out = []
    for s in SETS:
        for c in _load(s):
            for q, want in c["expect"].items():
                if q == "tema_programa":        # program-topic choice: no deterministic equivalent (not built)
                    continue
                marks = []
                why = KNOWN_MISSES.get((s, c["name"], q))
                if why:
                    marks = [pytest.mark.xfail(strict=True, reason=why)]
                out.append(pytest.param(s, c, q, want, id=f"{s}:{c['name']}:{q}", marks=marks))
    return out


@pytest.mark.parametrize("set_name,case,question,want", _params())
def test_probe_case(set_name, case, question, want):
    got = predict(set_name, case)[question]
    msg = case["state"]
    assert got == want, f"{msg}: {question} = {got!r}, expected {want!r}"


def test_no_topic_expectation_is_silently_dropped():
    """tema_programa is the only expectation not graded; count them so a new one cannot hide."""
    n = sum(1 for s in SETS for c in _load(s) for q in c["expect"] if q == "tema_programa")
    assert n == 3


# ------------------------------------------------------------------ post-hoc held-out (written after the rules)
LOCAL = json.loads((EV / "local_holdout_det.json").read_text(encoding="utf-8"))
# First (frozen-rules) score of this set, before the misses were fixed: intent 58/60, opt-in 2/2, cause 9/12,
# pre-registro 4/6. The fixes (list wording for opt-out, "reporto", "heladota", "el agua se llevó",
# "no el granizo", receipt-talk, "pues sí / lo llené") were general rules, but this set is no longer held-out.


def _local_params():
    out = []
    for c in LOCAL["j1"]:
        for lab, want in c["expect"].items():
            out.append(pytest.param("j1", c, lab, want, id=f"local:{c['name']}:{lab}"))
    for c in LOCAL["optin"]:
        out.append(pytest.param("optin", c, "optin", c["expect"], id=f"local:{c['name']}:optin"))
    for c in LOCAL["j3"]:
        for q, want in c["expect"].items():
            out.append(pytest.param("j3", c, q, want, id=f"local:{c['name']}:{q}"))
    for c in LOCAL["j2"]:
        out.append(pytest.param("j2", c, "prerregistro", c["expect"], id=f"local:{c['name']}"))
    return out


@pytest.mark.parametrize("kind,case,question,want", _local_params())
def test_local_holdout(kind, case, question, want):
    if kind in ("j1", "optin"):
        got = classify(case["mensaje"], today=TODAY, parcel_area=AREA).labels[question]
    elif kind == "j3":
        s = RegexSlotFiller().fill(case["mensaje"], TODAY, AREA)
        got = {"causa": s["cause"] or "no_dicha", "cultivo": s["crop"] or "no_dicho"}[question]
    else:
        got = read_preregistro(case["respuesta"])
    assert got == want, f"{case}: {question} = {got!r}"


def scores() -> dict:
    res = {}
    for s in SETS:
        ok = tot = 0
        misses = []
        for c in _load(s):
            p = predict(s, c)
            for q, want in c["expect"].items():
                if q == "tema_programa":
                    continue
                tot += 1
                if p[q] == want:
                    ok += 1
                else:
                    misses.append(f"{c['name']}/{q}: got {p[q]} want {want}")
        res[s] = (ok, tot, misses)
    return res


if __name__ == "__main__":
    for s, (ok, tot, misses) in scores().items():
        print(f"{s:28s} {ok:3d}/{tot:<3d}")
        for m in misses:
            print("    MISS", m)
