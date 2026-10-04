"""Inference: predict(), climatology(), backtest_report(). Offline once a forecast dict is passed."""
from __future__ import annotations

import copy
import gzip
import json
import math
import os
from functools import lru_cache

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import calib
from .features import WEATHER, night_from_scalars
from .terrain import load_dem, point_features

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
SUPPORT = {1.0: "station-anchored", 0.0: "terrain-transfer"}
DOMAIN = dict(lat=(18.9, 20.1), lon=(-100.3, -99.2))
LAPSE_C_PER_M = 0.0065


# Model variants. None/"shipped" = the product artifacts (trained on all data). "holdout_2025_26" = trained only on
# nights before 2025-10-01 (research/09_holdout_artifact.py), for replaying 2025-26 nights without in-sample leakage.
VARIANTS = {None: "", "shipped": "", "holdout_2025_26": "holdout_2025_26"}


@lru_cache(maxsize=4)
def _art(variant: str | None = None):
    if variant not in VARIANTS:
        raise ValueError(f"unknown model variant {variant!r}; choose one of {sorted(k for k in VARIANTS if k)}")
    d = os.path.join(ART, VARIANTS[variant])
    meta = json.load(open(os.path.join(d, "model_meta.json")))
    models = {}
    for k in ("anchor", "transfer_wx", "transfer_terrain"):
        fn = {"anchor": "model_anchor.txt.gz", "transfer_wx": "model_transfer_wx.txt.gz",
              "transfer_terrain": "model_transfer_terrain.txt.gz"}[k]
        with gzip.open(os.path.join(d, fn), "rt") as fh:
            models[k] = lgb.Booster(model_str=fh.read())
    st = pd.DataFrame(meta["stations"]).T
    for c in st.columns:
        if c not in ("name", "period"):
            st[c] = st[c].astype(float)
    return dict(meta=meta["meta"], stations=st, models=models, dem=_dem(), clim=_clim())


@lru_cache(maxsize=1)
def _dem():
    return load_dem(os.path.join(ART, "dem_6s.npz"))


@lru_cache(maxsize=1)
def _clim():
    return json.load(open(os.path.join(ART, "climatology.json")))


def _km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def _site(p, A=None):
    """Terrain features + anchoring decision for a parcel."""
    A = A or _art()
    if not (DOMAIN["lat"][0] <= p.lat <= DOMAIN["lat"][1] and DOMAIN["lon"][0] <= p.lon <= DOMAIN["lon"][1]):
        raise ValueError(f"parcel {p.parcel_id} ({p.lat}, {p.lon}) is outside the Toluca-valley model domain {DOMAIN}")
    t = point_features(A["dem"], p.lat, p.lon)
    st = A["stations"]
    d = _km(p.lat, p.lon, st.lat.values, st.lon.values)
    i = int(np.argmin(d))
    rule = A["meta"]["anchor_rule"]
    anchored = d[i] <= rule["max_km"] and abs(t["elev"] - st.elev.values[i]) <= rule["max_dz_m"]
    return t, st.index[i], float(d[i]), anchored


def _predict_rows(rows: pd.DataFrame, anchored: np.ndarray, station_ids, A=None) -> np.ndarray:
    A = A or _art(); f = A["meta"]["features"]; st = A["stations"]
    out = np.empty(len(rows))
    if anchored.any():
        r = rows[anchored]
        sid = [station_ids[k] for k in np.flatnonzero(anchored)]
        base = A["models"]["anchor"].predict(r[f["anchor"]].values.astype(float))
        a = st.loc[sid, "a"].values; b = st.loc[sid, "b"].values
        out[anchored] = base + a + b * r.clear_calm.values
    if (~anchored).any():
        r = rows[~anchored]
        out[~anchored] = 0.5 * (A["models"]["transfer_wx"].predict(r[f["transfer_wx"]].values.astype(float))
                                + A["models"]["transfer_terrain"].predict(r[f["transfer_terrain"]].values.astype(float)))
    return out


def _weather(fd: dict, date, t: dict):
    """Weather feature row for one night + the lapse adjustment applied (see predict)."""
    fd = dict(fd)
    ge = fd.get("grid_elev_m")
    adj = 0.0
    if ge is not None and not (isinstance(ge, float) and math.isnan(ge)):
        adj = -LAPSE_C_PER_M * (t["elev"] - float(ge))
        for k in ("tmin_c", "grid_tmin_c", "fc_tmin", "tmax_prev_c", "tmax_c", "fc_tmax_prev"):
            if k in fd and fd[k] is not None:
                fd[k] = float(fd[k]) + adj
    return night_from_scalars(fd, date), adj


def _transfer_features(w: dict, t: dict, p) -> dict:
    feats = dict(w); feats.update(t); feats.update(lat=p.lat, lon=p.lon)
    return feats


def _forecast_for(p, forecast, date, A=None):
    if forecast is None:
        from . import forecast as fc
        return fc.fetch_night(p.lat, p.lon, date, model=(A or _art())["meta"]["forecast_source"])
    if p.parcel_id in forecast and isinstance(forecast[p.parcel_id], dict):
        return forecast[p.parcel_id]
    return forecast


def predict(parcels, date: str, forecast: dict | None = None, variant: str | None = None, sites=None):
    """Parcel-level night Tmin + calibrated P(Tmin <= 0 degC) for the night starting on the evening of `date`.

    forecast: None -> fetch from Open-Meteo (network). Otherwise either one scalar dict applied to every parcel, or
    {parcel_id: scalar dict}. Scalar dict keys: tmin_c (required; night minimum 18:00-08:00 of the grid forecast),
    dew_c, cloud_pct, wind_kmh (night means), tmax_prev_c (afternoon max). Missing optional keys use neutral
    defaults and are counted in drivers['forecast_fallback'].
    variant: None (shipped model) or "holdout_2025_26" (trained without the 2025-26 season; for honest replays of
    2025-26 nights). drivers['variant_holdout_2025_26'] is 1.0 when the held-out variant answered.
    sites: optional {parcel_id: SiteCalibration} (or a list) from calibrate_site(), i.e. a parcel's own logger
    readings. A terrain-transfer parcel with >= 1 logger night BEFORE `date` becomes "logger-anchored (k noches)":
    terrain-transfer prediction + its empirical-Bayes site correction, with the interval measured for k nights
    (research/10_logger_learning_curve.py). Station-anchored parcels keep the station regime (validated better).
    """
    from . import FrostForecast
    from . import logger as lg
    if sites is not None and not isinstance(sites, dict):
        sites = {s.parcel_id: s for s in sites}
    sites = sites or {}
    A = _art(variant)
    if not parcels:
        return []
    rows, sites_ = [], []
    lapse = []
    batch = None
    if forecast is None and len(parcels) > 1:
        from . import forecast as fc
        try:  # one HTTP call for all parcels; per-parcel fetch_night stays as the fallback
            batch = fc.fetch_nights([(p.lat, p.lon) for p in parcels], date, model=A["meta"]["forecast_source"])
        except Exception:
            batch = None
    for k0, p in enumerate(parcels):
        fd = dict(batch[k0] if batch is not None else _forecast_for(p, forecast, date, A))
        t, sid, dkm, anch = _site(p, A)
        # If the forecast came from a grid cell whose elevation differs from the parcel (e.g. Open-Meteo with
        # elevation=nan), bring it to the parcel elevation with the standard 6.5 degC/km lapse rate, which is what
        # Open-Meteo's own point downscaling (used for all training data) does.
        w, adj = _weather(fd, date, t)
        lapse.append(adj)
        sites_.append((t, sid, dkm, anch))
        feats = dict(w)
        if anch:  # station-anchored: use the station's own site descriptors (exactly the validated setting)
            feats.update({k: float(A["stations"].loc[sid, k]) for k in A["meta"]["features"]["anchor"] if k not in WEATHER})
            feats.update(lat=float(A["stations"].loc[sid, "lat"]), lon=float(A["stations"].loc[sid, "lon"]))
        else:
            feats = _transfer_features(w, t, p)
        rows.append(feats)
    X = pd.DataFrame(rows)
    anchored = np.array([s[3] for s in sites_])
    pred = _predict_rows(X, anchored, [s[1] for s in sites_], A)
    cal = A["meta"]["calibration"]
    # logger-anchored parcels: terrain-transfer + the parcel's own site correction, fitted on nights before `date`
    logger = {}
    for k, p in enumerate(parcels):
        sc = sites.get(p.parcel_id)
        if sc is None or sites_[k][3]:
            continue
        fit = lg.fit_before(sc, p, date, variant)
        if fit is not None:
            logger[k] = fit
            pred[k] = pred[k] + fit["a"] + fit["b"] * float(X.clear_calm.iloc[k])
    out = []
    for k, p in enumerate(parcels):
        t, sid, dkm, anch = sites_[k]
        c = logger[k]["level"]["calibration"] if k in logger else cal["known_station" if anch else "unseen_site"]
        pf, lo, hi = calib.apply(c, [pred[k]], [X.clear_calm.iloc[k]])
        # widen for missing forecast inputs (each missing weather driver adds 0.5 degC per side)
        fb = float(X.fallback.iloc[k]); lo_k, hi_k = lo[0] - 0.5 * fb, hi[0] + 0.5 * fb
        st = A["stations"].loc[sid]
        drivers = {
            "support_station_anchored": 1.0 if anch else 0.0,
            "nearest_station_id": float(sid), "nearest_station_km": round(dkm, 2),
            "station_offset_c": round(float(st.a + st.b * X.clear_calm.iloc[k]), 2) if anch else 0.0,
            "elev_m": round(float(p.elev_m if p.elev_m is not None else t["elev"]), 1),
            "dem_elev_m": round(t["elev"], 1),
            "elev_diff_m": round(t["dz_10k"], 1),          # parcel minus mean terrain within 10 km (grid-cell scale)
            "tpi": round(t["tpi_1k"], 1), "tpi_3k": round(t["tpi_3k"], 1),
            "height_above_valley_floor_m": round(t["havf_5k"], 1),
            "slope_deg": round(t["slope_deg"], 2), "northness": round(t["northness"], 3),
            "horizon_deg": round(t["horizon_deg"], 2),
            "clear_sky": round(float(1 - X.fc_cloud.iloc[k] / 100.0), 3),
            "wind_kmh": round(float(X.fc_wind.iloc[k]), 2),
            "dewpoint_depression_c": round(float(X.fc_dep.iloc[k]), 2),
            "clear_calm": round(float(X.clear_calm.iloc[k]), 3),
            "correction_c": round(float(pred[k] - (X.fc_tmin.iloc[k] - lapse[k])), 2),
            "lapse_adj_c": round(float(lapse[k]), 2),
            "forecast_fallback": fb,
            "variant_holdout_2025_26": 1.0 if variant == "holdout_2025_26" else 0.0,
            "support_logger_anchored": 1.0 if k in logger else 0.0,
            "logger_nights": float(logger[k]["n"]) if k in logger else 0.0,
            "logger_offset_c": round(float(logger[k]["a"] + logger[k]["b"] * X.clear_calm.iloc[k]), 2) if k in logger else 0.0,
            "logger_expected_mae_c": float(logger[k]["level"]["mae_c"]) if k in logger else 0.0,
        }
        out.append(FrostForecast(parcel_id=p.parcel_id, date=str(pd.Timestamp(date).date()),
                                 grid_tmin_c=round(float(X.fc_tmin.iloc[k] - lapse[k]), 2), tmin_c=round(float(pred[k]), 2),
                                 tmin_lo_c=round(float(min(lo_k, pred[k])), 2), tmin_hi_c=round(float(max(hi_k, pred[k])), 2),
                                 p_frost=round(float(np.clip(pf[0], 0.0, 1.0)), 3), drivers={k2: float(v) for k2, v in drivers.items()}))
    return out


def support_of(x) -> str:
    """'station-anchored' | 'terrain-transfer' for a FrostForecast or a Parcel (documented extension of the contract).
    A forecast made with a logger calibration returns 'logger-anchored (k noches)'."""
    if hasattr(x, "drivers"):
        if x.drivers.get("support_logger_anchored"):
            return f"logger-anchored ({int(x.drivers['logger_nights'])} noches)"
        return SUPPORT[x.drivers["support_station_anchored"]]
    return SUPPORT[1.0 if _site(x)[3] else 0.0]


def climatology(parcel):
    """Frost-date percentiles (day of year) for the parcel, 1991-2025 SMN records.

    Station-anchored parcels use their station's record directly. Other parcels use up to 5 stations within 25 km
    (inverse-distance weights); each station's record is read at threshold tau = -delta, where delta is the modelled
    mean difference (parcel minus station) over 64 reference cold nights (terrain-transfer model at the parcel vs the
    station-anchored model at the station). Percentile semantics: last_spring p90 = in 9 of 10 years the last frost
    came before this day; first_autumn p10 = in 1 of 10 years the first frost came before this day.
    """
    from . import FrostClimatology
    A = _art(); C = A["clim"]; taus = np.array(C["taus"], float)
    t, sid, dkm, anch = _site(parcel)
    cs = pd.DataFrame({s: dict(lat=v["lat"], lon=v["lon"]) for s, v in C["stations"].items()}).T.astype(float)
    if anch and sid in C["stations"]:
        picks = [(sid, 1.0, 0.0)]
    else:
        d = _km(parcel.lat, parcel.lon, cs.lat.values, cs.lon.values)
        order = [i for i in np.argsort(d)[:5] if d[i] <= 25.0] or [int(np.argmin(d))]
        ref = pd.DataFrame(A["meta"]["reference_cold_nights"])
        # parcel under terrain-transfer
        Xp = ref.assign(**t, lat=parcel.lat, lon=parcel.lon)
        tp = _predict_rows(Xp, np.zeros(len(Xp), bool), [None] * len(Xp)).mean()
        picks = []
        for i in order:
            s = cs.index[i]
            if s in A["stations"].index:
                row = A["stations"].loc[s]
                Xs = ref.assign(**{k: float(row[k]) for k in t}, lat=float(row.lat), lon=float(row.lon))
                ts = _predict_rows(Xs, np.ones(len(Xs), bool), [s] * len(Xs)).mean()
            else:  # station with long record but no forecast-era data: compare terrain-transfer at both sites
                ts_t = point_features(A["dem"], float(cs.lat.iloc[i]), float(cs.lon.iloc[i]))
                Xs = ref.assign(**ts_t, lat=float(cs.lat.iloc[i]), lon=float(cs.lon.iloc[i]))
                ts = _predict_rows(Xs, np.zeros(len(Xs), bool), [None] * len(Xs)).mean()
            picks.append((s, 1.0 / max(d[i], 1.0) ** 2, float(tp - ts)))
    acc = dict(ls_p50=0.0, ls_p90=0.0, fa_p10=0.0, fa_p50=0.0, ffd_p50=0.0); wsum = 0.0
    for s, w, delta in picks:
        tau = float(np.clip(-delta, taus.min(), taus.max()))
        rec = C["stations"][s]["tau"]
        for k in acc:
            acc[k] += w * float(np.interp(tau, taus, [rec[str(int(x))][k] for x in taus]))
        wsum += w
    v = {k: int(round(x / wsum)) for k, x in acc.items()}
    return FrostClimatology(parcel_id=parcel.parcel_id, last_spring_frost_doy_p50=v["ls_p50"],
                            last_spring_frost_doy_p90=max(v["ls_p90"], v["ls_p50"]),
                            first_autumn_frost_doy_p10=min(v["fa_p10"], v["fa_p50"]),
                            first_autumn_frost_doy_p50=v["fa_p50"], frost_free_days_p50=v["ffd_p50"])


def backtest_report() -> dict:
    """Evidence numbers for both regimes (see README). Pure read of artifacts/backtest.json."""
    return copy.deepcopy(_backtest())


@lru_cache(maxsize=1)
def _backtest():
    return json.load(open(os.path.join(ART, "backtest.json")))
