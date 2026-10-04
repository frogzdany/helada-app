"""Parcel loggers: a parcel's own nightly minima turn it into "logger-anchored (k noches)".

calibrate_site(parcel, observations) -> SiteCalibration
    observations = [(date, tmin_c), ...], one reading per night (a min-max thermometer read in the morning, or the
    nightly minimum of a data logger). `date` is the EVENING date of the night by default (the night of X runs
    X 18:00 -> X+1 08:00, as in predict()); pass date_is="morning" for readings dated by the morning they were taken.
    For each night we need the archived day-1 forecast the model uses as input: pass `forecasts`
    ({evening_date: scalar dict, as for predict()}) or let it fetch them from the Open-Meteo Previous Runs API.

The correction is r = a + b*clear_calm on top of the terrain-transfer prediction, estimated with an
empirical-Bayes posterior: prior N(0, Sigma) (so zero nights = the terrain-transfer prediction), night noise
sigma^2 inflated for night-to-night autocorrelation. Sigma, sigma^2, rho, and the interval calibration for each
number of nights k were measured on held-out SMN stations (research/10_logger_learning_curve.py ->
artifacts/logger_curve.json). A site with k nights uses the largest measured level <= k (conservative).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from functools import lru_cache

import numpy as np
import pandas as pd

TMIN_RANGE_C = (-25.0, 30.0)    # plausible nightly minimum at 2300-3000 m in central Mexico
ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


@lru_cache(maxsize=1)
def curve() -> dict:
    """The measured learning curve + EB hyperparameters (artifacts/logger_curve.json)."""
    return json.load(open(os.path.join(ART, "logger_curve.json")))


def level_for(k: int) -> dict:
    lv = sorted(curve()["levels"], key=lambda x: x["k"])
    return [x for x in lv if x["k"] <= max(int(k), 0)][-1]


def expected_precision(k: int) -> dict:
    """What the backtest measured for a site with k logger nights (held-out SMN stations, Jan-Mar nights)."""
    lv = level_for(k)
    return dict(k=int(k), level_k=int(lv["k"]), mae_c=round(float(lv["mae_c"]), 2),
                halfwidth80_c=round(float(lv["width80_c"]) / 2, 2), recall_at_pofd5=round(float(lv["recall5"]), 3),
                ceiling_mae_c=round(float(curve()["ceiling"]["mae_c"]), 2))


@dataclass
class SiteCalibration:
    parcel_id: str
    lat: float
    lon: float
    n_nights: int
    a_c: float                  # site correction on top of terrain-transfer: a + b*clear_calm (degC)
    b_c: float
    level_k: int                # measured learning-curve level used for the interval
    expected_mae_c: float       # measured Tmin MAE at that level
    halfwidth80_c: float        # measured half-width of the 80% interval at that level
    first_night: str | None
    last_night: str | None
    variant: str | None = None
    nights: list = field(default_factory=list)      # [{"date": evening, "tmin_c": x, "forecast": {...}}]
    rejected: list = field(default_factory=list)    # [{"date": ..., "tmin_c": ..., "reason": ...}]

    @property
    def support(self) -> str:
        return f"logger-anchored ({self.n_nights} noches)"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SiteCalibration":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def _iso(d) -> str:
    if isinstance(d, (_date, pd.Timestamp)):
        return pd.Timestamp(d).strftime("%Y-%m-%d")
    return pd.Timestamp(str(d).strip()).strftime("%Y-%m-%d")


def _residuals(parcel, nights: list[dict], variant):
    from . import core
    A = core._art(variant)
    t, *_ = core._site(parcel, A)
    rows = [core._transfer_features(core._weather(n["forecast"], n["date"], t)[0], t, parcel) for n in nights]
    X = pd.DataFrame(rows)
    base = core._predict_rows(X, np.zeros(len(X), bool), [None] * len(X), A)
    y = np.array([float(n["tmin_c"]) for n in nights])
    return X.clear_calm.values.astype(float), y - base


def posterior(cc, r) -> tuple[float, float, np.ndarray]:
    """EB posterior mean (a, b) and covariance of the site correction; no nights -> (0, 0)."""
    hp = curve()["hyper"]; S = np.asarray(hp["Sigma"], float)
    if len(r) == 0:
        return 0.0, 0.0, S
    X = np.c_[np.ones(len(cc)), np.asarray(cc, float)]
    w = 1.0 / (hp["sigma2"] * (1 + hp["rho"]) / (1 - hp["rho"]))
    C = np.linalg.inv(w * X.T @ X + np.linalg.inv(S))
    a, b = C @ (w * X.T @ np.asarray(r, float))
    return float(a), float(b), C


def calibrate_site(parcel, observations, forecasts: dict | None = None, variant: str | None = None,
                   date_is: str = "evening", timeout: float = 60.0) -> SiteCalibration:
    """Site calibration from a parcel's own nightly minima (see module docstring)."""
    from . import core
    from . import forecast as fc
    if date_is not in ("evening", "morning"):
        raise ValueError("date_is must be 'evening' or 'morning'")
    core._site(parcel)   # domain check
    obs, rejected = {}, []
    for item in observations:
        d, v = (item["date"], item["tmin_c"]) if isinstance(item, dict) else item
        try:
            ev = pd.Timestamp(_iso(d)) - pd.Timedelta(days=1 if date_is == "morning" else 0)
            ev = ev.strftime("%Y-%m-%d")
        except Exception:
            rejected.append(dict(date=str(d), tmin_c=v, reason="fecha no válida")); continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            rejected.append(dict(date=ev, tmin_c=v, reason="lectura no numérica")); continue
        if math.isnan(x) or not (TMIN_RANGE_C[0] <= x <= TMIN_RANGE_C[1]):
            rejected.append(dict(date=ev, tmin_c=v, reason=f"fuera de rango {TMIN_RANGE_C}")); continue
        obs[ev] = x                                    # a later reading for the same night replaces the earlier one
    fx = dict(forecasts or {})
    missing = [d for d in obs if d not in fx]
    if missing and forecasts is None:
        src = core._art(variant)["meta"]["forecast_source"]
        fx.update(fc.fetch_archive(parcel.lat, parcel.lon, min(missing), max(missing), model=src, timeout=timeout))
    nights = []
    for d in sorted(obs):
        if d in fx and fx[d] is not None:
            nights.append(dict(date=d, tmin_c=obs[d], forecast={k: v for k, v in fx[d].items()
                                                                if isinstance(v, (int, float, str, type(None)))}))
        else:
            rejected.append(dict(date=d, tmin_c=obs[d], reason="sin pronóstico archivado para esa noche"))
    cc, r = _residuals(parcel, nights, variant) if nights else (np.array([]), np.array([]))
    a, b, _ = posterior(cc, r)
    lv = level_for(len(nights))
    return SiteCalibration(parcel_id=parcel.parcel_id, lat=float(parcel.lat), lon=float(parcel.lon), n_nights=len(nights),
                           a_c=round(a, 4), b_c=round(b, 4), level_k=int(lv["k"]), expected_mae_c=round(float(lv["mae_c"]), 2),
                           halfwidth80_c=round(float(lv["width80_c"]) / 2, 2),
                           first_night=nights[0]["date"] if nights else None, last_night=nights[-1]["date"] if nights else None,
                           variant=variant, nights=nights, rejected=rejected)


def fit_before(sc: SiteCalibration, parcel, date, variant=None) -> dict | None:
    """The site correction usable for the night of `date`: only logger nights strictly BEFORE it (no leakage when
    replaying past nights), refitted if that drops nights or the model variant differs. None if no night remains."""
    if isinstance(sc, dict):
        sc = SiteCalibration.from_dict(sc)
    cut = _iso(date)
    nights = [n for n in sc.nights if n["date"] < cut]
    if not nights:
        return None
    if len(nights) == sc.n_nights and variant == sc.variant:
        a, b = sc.a_c, sc.b_c
    else:
        a, b, _ = posterior(*_residuals(parcel, nights, variant))
    return dict(a=a, b=b, n=len(nights), level=level_for(len(nights)))
