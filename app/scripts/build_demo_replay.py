"""Build the demo fixture backend/data/demo_replay.json: a REPLAY OF A REAL NIGHT from the held-out season.

Night: evening 2025-11-14 -> morning 2025-11-15 (SMN 08:00 reading dated 2025-11-15).
Chosen with model/research/out/product_ecmwf_ifs025_K_oos.csv.gz (forward holdout: model trained
Feb 2024 - Sep 2025, tested Oct 2025 - Mar 2026 at the same stations), as the night with the most
"archived forecast > +1 degC, station observed <= 0 degC" cases in Oct-Nov/Feb-Mar, 7 of 12 caught at p >= 0.3.

For each demo parcel:
  * station-anchored parcels (~0.9 km from an SMN station): the archived Open-Meteo `ecmwf_ifs025` day-1 forecast
    at the station's coordinates (Previous Runs API, the model's training input) + the station's observed Tmin
    + the held-out ("holdout") prediction for that station-night (cross-fitted calibration: fit on 2024-25).
    `grid_elev_m` = the elevation Open-Meteo downscaled that point forecast to, so helada_model moves it to the
    parcel's elevation at 6.5 degC/km (a few tenths of a degree at most here).
  * terrain-transfer parcels (> 6 km from any station): the same archived forecast fetched at the parcel's own
    coordinates (network, once). No observation exists there -- that is the point.

Needs ../model data (../data, research/out) and network for the two transfer parcels.
Run:  uv run python scripts/build_demo_replay.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

APP = Path(__file__).resolve().parents[1]
MODEL = APP.parent / "model"
DATA = APP.parent / "data"
sys.path.insert(0, str(MODEL / "src"))
from helada_model import calib  # noqa: E402
from helada_model.features import nights_from_hourly  # noqa: E402

EVENING = "2025-11-14"
MORNING = "2025-11-15"
SRC = "ecmwf_ifs025"
OUT = APP / "backend" / "data" / "demo_replay.json"
ROSTER = APP / "backend" / "data" / "roster.json"
PREV_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
VARS = ["temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m"]
NAMES = {"15282": "Tres Barrancas", "15010": "Atotonilco", "15086": "San Bernabé", "15201": "Trojes",
         "15126": "Toluca (Observatorio)", "15372": "Ixtlahuaca (DGE)", "15085": "San Bartolo del Llano",
         "15238": "Santa María del Llano", "15251": "Atlacomulco II (DGE)", "15026": "Enyeje"}


def fetch_archived(lat: float, lon: float) -> dict:
    r = httpx.get(PREV_RUNS, timeout=60, params=dict(
        latitude=f"{lat:.4f}", longitude=f"{lon:.4f}", start_date=EVENING, end_date=MORNING,
        timezone="America/Mexico_City", models=SRC, hourly=",".join(f"{v}_previous_day1" for v in VARS)))
    r.raise_for_status()
    j = r.json()
    h = pd.DataFrame(j["hourly"])
    h.columns = [c.replace("_previous_day1", "").replace(f"_{SRC}", "") for c in h.columns]
    n = nights_from_hourly(h)
    row = n[n.date == pd.Timestamp(MORNING)].iloc[0]
    return dict(fc_tmin=float(row.fc_tmin), fc_dew=float(row.fc_dew), fc_cloud=float(row.fc_cloud),
                fc_wind=float(row.fc_wind), fc_tmax_prev=float(row.fc_tmax_prev), grid_elev=float(j["elevation"]))


def fdict(r: dict, source: str) -> dict:
    """App/model forecast dict (keys helada_model and the app's TEMP_STUB both understand)."""
    return {"grid_tmin_c": round(r["fc_tmin"], 2), "dew_c": round(r["fc_dew"], 2),
            "cloud_cover": round(r["fc_cloud"], 1), "wind_kmh": round(r["fc_wind"], 2),
            "wind_ms": round(r["fc_wind"] / 3.6, 2), "tmax_prev_c": round(r["fc_tmax_prev"], 2),
            "grid_elev_m": round(r["grid_elev"], 1), "source": source}


def main() -> None:
    K = pd.read_csv(MODEL / "research" / "out" / f"product_{SRC}_K_oos.csv.gz", dtype={"station": str},
                    parse_dates=["date"])
    c24 = calib.fit(K.pred[K.win == 2024].values, K.tmin[K.win == 2024].values, K.clear_calm[K.win == 2024].values)
    T = K[(K.win == 2025) & (K.date == MORNING)].copy()
    p, lo, hi = calib.apply(c24, T.pred.values, T.clear_calm.values)
    T["p_hold"], T["lo_hold"], T["hi_hold"] = p, lo, hi
    T = T.set_index("station")
    names = pd.read_csv(DATA / "smn" / "stations.csv", dtype={"station": str}).set_index("station")

    # night-level summary over all stations of the held-out table that night
    warm_frost = T[(T.fc_tmin > 1) & (T.tmin <= 0)]
    summary = {"stations": int(len(T)), "frost_stations": int((T.tmin <= 0).sum()),
               "frost_with_forecast_above_1c": int(len(warm_frost)),
               "caught_p_ge_0_3": int((warm_frost.p_hold >= 0.3).sum()),
               "false_alarms_p_ge_0_3": int(((T.tmin > 0) & (T.p_hold >= 0.3)).sum())}

    parcels = json.loads(ROSTER.read_text(encoding="utf-8"))["parcels"]
    out = {}
    for pc in parcels:
        sid = pc.get("near_station")
        if sid:
            r = T.loc[sid]
            s = names.loc[sid]
            obs = float(r.tmin)
            hold_p = float(r.p_hold)
            fc = float(r.fc_tmin)
            outcome = ("hit" if obs <= 0 and hold_p >= 0.3 else "miss" if obs <= 0 else
                       "false_alarm" if hold_p >= 0.3 else "correct_negative")
            out[pc["parcel_id"]] = {
                "forecast": fdict(dict(fc_tmin=fc, fc_dew=r.fc_dew, fc_cloud=r.fc_cloud, fc_wind=r.fc_wind,
                                       fc_tmax_prev=r.fc_tmax_prev, grid_elev=r.grid_elev),
                                  f"replay: Open-Meteo {SRC} day-1 (archived) at SMN {sid}"),
                "station": {"id": sid, "name": NAMES.get(sid, str(s["name"]).title()), "lat": float(s.lat), "lon": float(s.lon),
                            "alt_m": float(s.alt)},
                "observed_tmin_c": obs,
                "holdout": {"tmin_c": round(float(r.pred), 2), "tmin_lo_c": round(float(r.lo_hold), 2),
                            "tmin_hi_c": round(float(r.hi_hold), 2), "p_frost": round(hold_p, 3),
                            "outcome": outcome},
            }
        else:
            r = fetch_archived(pc["lat"], pc["lon"])
            out[pc["parcel_id"]] = {
                "forecast": fdict(r, f"replay: Open-Meteo {SRC} day-1 (archived) at the parcel"),
                "station": None, "observed_tmin_c": None, "holdout": None,
            }
        print(pc["parcel_id"], json.dumps(out[pc["parcel_id"]], ensure_ascii=False))

    fixture = {
        "label": "Réplica de una noche real: 14 → 15 de noviembre de 2025",
        "night_date": EVENING,
        "observation_date": MORNING,
        "forecast_source": f"Open-Meteo Previous Runs API, model {SRC}, day-1 run (the model's training input); "
                           "CC BY 4.0",
        "observation_source": "CONAGUA/SMN daily climatology, 08:00 manual reading dated " + MORNING,
        "holdout_note": "La réplica usa helada_model variant='holdout_2025_26' (entrenado solo con noches hasta sep 2025, "
                        "calibración de 2024-25): el modelo no vio esta noche. «holdout» = la predicción del backtest "
                        "para la estación, idéntica a lo que muestra la réplica.",
        "night_summary": summary,
        "parcels": out,
    }
    OUT.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", OUT, summary)


if __name__ == "__main__":
    main()
