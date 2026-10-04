"""Demo scenario = REPLAY OF A REAL NIGHT from the model's held-out season (not invented numbers).

Fixture: backend/data/demo_replay.json, built by scripts/build_demo_replay.py from
  * the archived Open-Meteo `ecmwf_ifs025` day-1 forecast (Previous Runs API; the model's own input),
  * the SMN observed Tmin at the station next to each station-anchored parcel,
  * the backtest's held-out prediction for that station-night (model trained before Oct 2025).
The night date is fixed (the fixture's), because the forecast values belong to that night.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from .config import BACKEND_DIR

REPLAY_FILE = BACKEND_DIR / "data" / "demo_replay.json"


@lru_cache(maxsize=1)
def replay() -> dict:
    return json.loads(Path(REPLAY_FILE).read_text(encoding="utf-8"))


def demo_night() -> str:
    return replay()["night_date"]


def _nearest_fixture_parcel(p: dict) -> str | None:
    """For a parcel added to the roster after the fixture was built: borrow the closest fixture forecast."""
    from .roster import load_roster_file
    pos = {q["parcel_id"]: (q["lat"], q["lon"]) for q in load_roster_file()}
    best, best_d = None, math.inf
    for pid in replay()["parcels"]:
        if pid in pos:
            d = (pos[pid][0] - p["lat"]) ** 2 + (pos[pid][1] - p["lon"]) ** 2
            if d < best_d:
                best, best_d = pid, d
    return best


def demo_forecast(parcels: list[dict]) -> dict:
    """{parcel_id: forecast dict} for the replayed night (keys understood by helada_model and the stub)."""
    fx = replay()["parcels"]
    out = {}
    for p in parcels:
        pid = p["parcel_id"] if p["parcel_id"] in fx else _nearest_fixture_parcel(p)
        if pid is None:
            continue
        f = dict(fx[pid]["forecast"])
        if pid != p["parcel_id"]:
            f["source"] += f" (borrowed from {pid})"
        out[p["parcel_id"]] = f
    return out


def demo_truth(parcel_id: str) -> dict | None:
    """Observed SMN Tmin + held-out prediction for a fixture parcel (None for terrain-transfer parcels)."""
    e = replay()["parcels"].get(parcel_id)
    if not e:
        return None
    return {"station": e["station"], "observed_tmin_c": e["observed_tmin_c"], "holdout": e["holdout"]}


def replay_info() -> dict:
    r = replay()
    return {k: r[k] for k in ("label", "night_date", "observation_date", "forecast_source", "observation_source",
                              "holdout_note", "night_summary")}
