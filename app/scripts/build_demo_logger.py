"""Build backend/data/demo_logger.json: the worked example of «Registrador en la parcela».

A real SMN station the model NEVER saw stands in for a parcel logger: 15390 E.T.A. 013 Jocotitlán (not in the
training station table of either the shipped or the holdout_2025_26 artifact; no station within 1.5 km, so
helada_model treats its coordinates as terrain-transfer). Its 2025-26 frost-season daily minima (CONAGUA/SMN 08:00
manual reading) play the logger readings; the archived Open-Meteo `ecmwf_ifs025` day-1 forecast at its
coordinates (Previous Runs API, the model's input) is fetched once.

season_check (precomputed with variant="holdout_2025_26", the model trained without 2025-26):
  * cut: readings of the nights before the replay night (Oct 1 - Nov 13, 2025) calibrate the site; every later
    night of the season is predicted with that fixed calibration -> MAE / 80% coverage before vs after.
  * online: every night is predicted with all readings of the nights before it (the calibration grows nightly).
One site, one season: an illustration, not evidence. The evidence is model/research/10_logger_learning_curve.py.

Needs ../data/smn/obs_daily.csv.gz (model/research/01_fetch_smn.py) and network.
Run:  uv run python scripts/build_demo_logger.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

APP = Path(__file__).resolve().parents[1]
DATA = APP.parent / "data"
sys.path.insert(0, str(APP.parent / "model" / "src"))
import helada_model as hm  # noqa: E402
from helada_model import forecast as hf  # noqa: E402

STATION, LAT, LON, ALT = "15390", 19.71111, -99.78889, 2650
REPLAY = "2025-11-14"
FIRST, LAST = "2025-10-01", "2026-03-31"          # evening dates of the 2025-26 frost season
VARIANT = "holdout_2025_26"
OUT = APP / "backend" / "data" / "demo_logger.json"


def main():
    o = pd.read_csv(DATA / "smn" / "obs_daily.csv.gz", dtype={"station": str}, parse_dates=["date"])
    o = o[(o.station == STATION)].dropna(subset=["tmin"])
    o["night_date"] = (o.date - pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d")
    o = o[(o.night_date >= FIRST) & (o.night_date <= LAST)].sort_values("night_date")
    fx = hf.fetch_archive(LAT, LON, FIRST, LAST, model="ecmwf_ifs025")
    p = hm.Parcel("R-JOCO", LAT, LON)
    assert hm.support_of(p) == "terrain-transfer"
    obs = [(d, float(t)) for d, t in zip(o.night_date, o.tmin) if d in fx]
    y = dict(obs)

    def run(nights, calib_for):
        e0, e1, c0, c1, rows = [], [], 0, 0, []
        for d in nights:
            b = hm.predict([p], d, forecast=fx[d], variant=VARIANT)[0]
            sc = calib_for(d)
            a = hm.predict([p], d, forecast=fx[d], variant=VARIANT, sites=[sc])[0] if sc else b
            e0.append(abs(b.tmin_c - y[d])); e1.append(abs(a.tmin_c - y[d]))
            c0 += b.tmin_lo_c <= y[d] <= b.tmin_hi_c; c1 += a.tmin_lo_c <= y[d] <= a.tmin_hi_c
            rows.append((b.tmin_c <= 0, a.tmin_c <= 0, y[d] <= 0))
        n = len(nights)
        return dict(nights=n, frost_nights=int(sum(r[2] for r in rows)), mae_before_c=round(float(np.mean(e0)), 2),
                    mae_after_c=round(float(np.mean(e1)), 2), cov80_before=round(c0 / n, 2), cov80_after=round(c1 / n, 2))

    cut_obs = [(d, t) for d, t in obs if d < REPLAY]
    sc_cut = hm.calibrate_site(p, cut_obs, forecasts=fx, variant=VARIANT)
    later = [d for d, _ in obs if d >= REPLAY]
    cut = run(later, lambda d: sc_cut)
    sc_all = hm.calibrate_site(p, obs, forecasts=fx, variant=VARIANT)   # fit_before() keeps only nights < d
    online = run([d for d, _ in obs[1:]], lambda d: sc_all)
    fixture = {
        "site": {"station": STATION, "label": "Registrador de ejemplo en E.T.A. 013 Jocotitlán", "lat": LAT, "lon": LON,
                 "alt_m": ALT, "municipality": "Jocotitlán"},
        "note": ("Ejemplo con datos reales: las mínimas diarias de la estación SMN 15390 (E.T.A. 013 Jocotitlán), que el "
                 "modelo nunca vio, hacen de registrador. Una sola estación y una temporada: ilustra, no prueba. La "
                 "evidencia es la curva medida en 80 estaciones (model/README.md)."),
        "source": {"observations": "CONAGUA/SMN daily climatology, 08:00 manual reading (night = evening before)",
                   "forecasts": "Open-Meteo Previous Runs API, ecmwf_ifs025 day-1 (CC BY 4.0), at the station coordinates",
                   "model_variant": VARIANT},
        "replay_night": REPLAY,
        "observations": [{"night_date": d, "tmin_c": t} for d, t in obs],
        "forecasts": {d: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in fx[d].items()} for d in sorted(fx)},
        "season_check": {"cut": dict(k=sc_cut.n_nights, first_night=REPLAY, **cut),
                         "online": dict(**online, note="each night uses all readings of the nights before it")},
    }
    OUT.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(fixture["season_check"], indent=1), OUT, OUT.stat().st_size // 1000, "KB")


if __name__ == "__main__":
    main()
