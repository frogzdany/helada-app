"""Optional online fetch of the night forecast from Open-Meteo (only used when predict() gets no forecast dict).

Endpoint: https://api.open-meteo.com/v1/forecast (free, no key; CC BY 4.0 data). We request the same hourly
variables the model was trained on and aggregate them with the SMN night convention (18:00 -> 08:00 local).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

import pandas as pd

from .features import nights_from_hourly

URL = "https://api.open-meteo.com/v1/forecast"
VARS = ["temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m"]


def _night_from_response(j: dict, d0: pd.Timestamp, model: str) -> dict:
    """Parse one Open-Meteo location object (hourly + elevation) into the scalar night dict."""
    h = pd.DataFrame(j["hourly"])
    h.columns = [c.replace(f"_{model}", "") for c in h.columns]
    n = nights_from_hourly(h)
    row = n[n.date == d0 + pd.Timedelta(days=1)]
    if row.empty:
        raise RuntimeError("Open-Meteo returned no complete night window")
    r0 = row.iloc[0]
    return dict(tmin_c=float(r0.fc_tmin), dew_c=float(r0.fc_dew), cloud_pct=float(r0.fc_cloud),
                wind_kmh=float(r0.fc_wind), tmax_prev_c=float(r0.fc_tmax_prev), grid_elev_m=float(j.get("elevation", "nan")))


def _query(lat, lon, d0: pd.Timestamp, model: str) -> dict:
    return dict(latitude=lat, longitude=lon, hourly=",".join(VARS), timezone="America/Mexico_City",
                start_date=d0.strftime("%Y-%m-%d"), end_date=(d0 + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), models=model)


def fetch_night(lat: float, lon: float, evening_date: str, model: str = "best_match", timeout: float = 20.0) -> dict:
    """Return the scalar forecast dict for the night starting on `evening_date` at (lat, lon)."""
    d0 = pd.Timestamp(evening_date)
    q = _query(f"{lat:.5f}", f"{lon:.5f}", d0, model)
    with urllib.request.urlopen(f"{URL}?{urllib.parse.urlencode(q)}", timeout=timeout) as r:
        j = json.loads(r.read().decode())
    return _night_from_response(j, d0, model)


def parse_batch(j, n: int, evening_date: str, model: str = "best_match") -> list[dict]:
    """Parse a multi-location Open-Meteo response (list of objects; a bare dict is accepted when n == 1)."""
    if isinstance(j, dict):
        j = [j]
    if len(j) != n:
        raise RuntimeError(f"Open-Meteo returned {len(j)} locations for {n} requested")
    d0 = pd.Timestamp(evening_date)
    return [_night_from_response(x, d0, model) for x in j]


def fetch_nights(parcels_latlon, evening_date: str, model: str = "best_match", timeout: float = 30.0) -> list[dict]:
    """One HTTP call for many points. `parcels_latlon` is a sequence of (lat, lon); returns dicts in the same order
    (same keys as fetch_night, including grid_elev_m per location)."""
    pts = list(parcels_latlon)
    if not pts:
        return []
    d0 = pd.Timestamp(evening_date)
    q = _query(",".join(f"{a:.5f}" for a, _ in pts), ",".join(f"{b:.5f}" for _, b in pts), d0, model)
    with urllib.request.urlopen(f"{URL}?{urllib.parse.urlencode(q)}", timeout=timeout) as r:
        j = json.loads(r.read().decode())
    return parse_batch(j, len(pts), evening_date, model)


ARCHIVE_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"


def parse_archive(j: dict, model: str = "ecmwf_ifs025") -> dict[str, dict]:
    """Parse a Previous Runs API response (hourly *_previous_day1) into {evening_date: scalar night dict}."""
    h = pd.DataFrame(j["hourly"])
    h.columns = [c.replace("_previous_day1", "").replace(f"_{model}", "") for c in h.columns]
    n = nights_from_hourly(h)
    out = {}
    for _, r0 in n.iterrows():
        ev = (pd.Timestamp(r0.date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        d = dict(tmin_c=float(r0.fc_tmin), dew_c=float(r0.fc_dew), cloud_pct=float(r0.fc_cloud), wind_kmh=float(r0.fc_wind),
                 tmax_prev_c=None if pd.isna(r0.fc_tmax_prev) else float(r0.fc_tmax_prev),
                 grid_elev_m=float(j.get("elevation", "nan")), source=f"open-meteo previous-runs {model} day-1")
        if not any(pd.isna(v) for k, v in d.items() if k in ("tmin_c",)):
            out[ev] = d
    return out


def fetch_archive(lat: float, lon: float, first_evening: str, last_evening: str, model: str = "ecmwf_ifs025",
                  timeout: float = 60.0) -> dict[str, dict]:
    """Archived DAY-1 forecasts (the run ~24 h before, as used in training) for a range of past nights at a point:
    {evening_date: scalar night dict}. One HTTP call to the Open-Meteo Previous Runs API (free, CC BY 4.0)."""
    d0, d1 = pd.Timestamp(first_evening), pd.Timestamp(last_evening) + pd.Timedelta(days=1)
    q = dict(latitude=f"{lat:.5f}", longitude=f"{lon:.5f}", start_date=d0.strftime("%Y-%m-%d"), end_date=d1.strftime("%Y-%m-%d"),
             timezone="America/Mexico_City", models=model, hourly=",".join(f"{v}_previous_day1" for v in VARS))
    with urllib.request.urlopen(f"{ARCHIVE_URL}?{urllib.parse.urlencode(q)}", timeout=timeout) as r:
        j = json.loads(r.read().decode())
    return parse_archive(j, model)
