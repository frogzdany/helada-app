"""Night-level weather features from an hourly forecast (Open-Meteo layout).

Night convention (matches SMN): the night of evening date X runs X 18:00 -> X+1 08:00 local time.
SMN's Tmin reading dated X+1 (08:00) covers that night. The public API uses the EVENING date X.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

WEATHER = ["fc_tmin", "fc_dew", "fc_cloud", "fc_wind", "fc_dtr", "fc_dep", "clear_calm", "doy_s", "doy_c"]


def clear_calm(cloud_pct, wind_kmh):
    """0..1 index of radiative-cooling nights: clear sky AND weak wind."""
    c = np.clip(1.0 - np.asarray(cloud_pct, dtype=float) / 100.0, 0, 1)
    w = np.exp(-np.asarray(wind_kmh, dtype=float) / 8.0)
    return c * w


def add_derived(df: pd.DataFrame, morning_date_col: str = "date") -> pd.DataFrame:
    df = df.copy()
    doy = pd.to_datetime(df[morning_date_col]).dt.dayofyear
    df["doy_s"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_c"] = np.cos(2 * np.pi * doy / 365.25)
    df["fc_dtr"] = df["fc_tmax_prev"] - df["fc_tmin"]
    df["fc_dep"] = df["fc_tmin"] - df["fc_dew"]
    df["clear_calm"] = clear_calm(df["fc_cloud"], df["fc_wind"])
    return df


def nights_from_hourly(h: pd.DataFrame) -> pd.DataFrame:
    """h: columns time (local, naive), temperature_2m, dew_point_2m, cloud_cover, wind_speed_10m [, key cols].
    Returns one row per night keyed by `date` = MORNING date (SMN convention); caller converts if needed."""
    keys = [c for c in h.columns if c in ("station", "parcel_id")]
    h = h.copy(); h["time"] = pd.to_datetime(h["time"])
    shifted = h["time"] + pd.Timedelta(hours=6)
    h["D"] = shifted.dt.normalize(); h["hh"] = shifted.dt.hour
    night = h[h.hh <= 14]  # 18:00 (prev) .. 08:00
    g = night.groupby(keys + ["D"])
    f = pd.DataFrame({
        "fc_tmin": g.temperature_2m.min(),
        "fc_n": g.temperature_2m.count(),
        "fc_dew": g.dew_point_2m.mean(),
        "fc_cloud": g.cloud_cover.mean(),
        "fc_wind": g.wind_speed_10m.mean(),
    })
    aft = h[h["time"].dt.hour.between(12, 17)].copy()
    aft["D"] = aft["time"].dt.normalize() + pd.Timedelta(days=1)
    f = f.join(aft.groupby(keys + ["D"]).temperature_2m.max().rename("fc_tmax_prev"))
    f = f.reset_index().rename(columns={"D": "date"})
    return f[f.fc_n >= 12]


def magnus_dewpoint(t_c: float, rh_pct: float) -> float:
    a, b = 17.62, 243.12
    g = math.log(max(min(rh_pct, 100.0), 1.0) / 100.0) + a * t_c / (b + t_c)
    return b * g / (a - g)


def night_from_scalars(d: dict, evening_date: str) -> dict:
    """Build the weather feature row from a scalar forecast dict.
    Accepted keys (aliases): tmin_c|grid_tmin_c|fc_tmin, dew_c|dewpoint_c|fc_dew (or rh|rh_pct night-mean RH %),
    cloud_pct|cloud_cover|fc_cloud, wind_kmh|wind_speed_10m|fc_wind (or wind_ms in m/s), tmax_prev_c|tmax_c|fc_tmax_prev. Missing humidity/cloud/wind fall back to
    neutral climatological values (documented; the interval widens via `fallback` flag)."""
    def pick(*ks, default=None):
        for k in ks:
            if k in d and d[k] is not None and not (isinstance(d[k], float) and math.isnan(d[k])):
                return float(d[k])
        return default
    tmin = pick("tmin_c", "grid_tmin_c", "fc_tmin", "temperature_2m_min")
    if tmin is None:
        raise ValueError("forecast needs a night minimum temperature (tmin_c)")
    fallback = 0
    dew = pick("dew_c", "dewpoint_c", "dew_point_c", "fc_dew")
    if dew is None:
        rh = pick("rh", "rh_pct", "relative_humidity", "relative_humidity_2m")
        if rh is not None:  # night-mean RH -> dew point (Magnus) at an approximate night-mean T of tmin + 3 degC
            dew = magnus_dewpoint(tmin + 3.0, rh)
        else:
            dew = tmin - 4.0; fallback += 1
    cloud = pick("cloud_pct", "cloud_cover", "fc_cloud")
    if cloud is None: cloud = 40.0; fallback += 1
    wind = pick("wind_kmh", "wind_speed_10m", "fc_wind")
    if wind is None:
        wms = pick("wind_ms", "wind_speed_ms")
        if wms is not None:
            wind = 3.6 * wms
        else:
            wind = 6.0; fallback += 1
    tmax = pick("tmax_prev_c", "tmax_c", "fc_tmax_prev", "temperature_2m_max")
    if tmax is None: tmax = tmin + 16.0; fallback += 1
    morning = pd.Timestamp(evening_date) + pd.Timedelta(days=1)
    row = pd.DataFrame([dict(date=morning, fc_tmin=tmin, fc_dew=dew, fc_cloud=cloud, fc_wind=wind, fc_tmax_prev=tmax)])
    out = add_derived(row).iloc[0].to_dict()
    out["fallback"] = fallback
    return out
