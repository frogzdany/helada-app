"""Adapter between the app and `helada_model` (see docs/interfaces.md).

The app ONLY talks to the model through the three functions at the bottom:
`predict()`, `climatology()`, `backtest_report()`. They take/return plain dicts
so the rest of the app does not depend on the model's dataclasses.

If `helada_model` is importable (installed in this venv, or found under
HELADA_MODEL_PATH / ../model), it is used. Otherwise a clearly marked
TEMP STUB runs: raw Open-Meteo grid forecast (elevation=nan, i.e. no
downscaling) + a crude nocturnal lapse-rate / cold-air-pooling / clear-sky
adjustment. THE STUB IS NOT THE MODEL AND HAS NO SKILL CLAIM.
"""
from __future__ import annotations

import dataclasses
import importlib
import logging
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date as Date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .config import APP_DIR

log = logging.getLogger("helada.model")


# ---------------------------------------------------------------- contract types
# Mirrors of the dataclasses in docs/interfaces.md, used by the stub (and for field lists).
@dataclass
class Parcel:
    parcel_id: str
    lat: float
    lon: float
    elev_m: float | None = None
    owner_name: str = ""
    phone: str = ""
    municipality: str = ""
    area_ha: float = 0.0
    crop: str = "maiz_temporal"
    lang: str = "es"


@dataclass
class FrostForecast:
    parcel_id: str
    date: str
    grid_tmin_c: float
    tmin_c: float
    tmin_lo_c: float
    tmin_hi_c: float
    p_frost: float
    drivers: dict[str, float] = field(default_factory=dict)


@dataclass
class FrostClimatology:
    parcel_id: str
    last_spring_frost_doy_p50: int
    last_spring_frost_doy_p90: int
    first_autumn_frost_doy_p10: int
    first_autumn_frost_doy_p50: int
    frost_free_days_p50: int


PARCEL_FIELDS = [f.name for f in dataclasses.fields(Parcel)]


# ---------------------------------------------------------------- real model import
def _try_import_model():
    try:
        return importlib.import_module("helada_model")
    except ImportError:
        pass
    candidates = [os.environ.get("HELADA_MODEL_PATH", "")]
    root = APP_DIR.parent / "model"
    candidates += [str(root / "src"), str(root)]
    for c in candidates:
        if c and Path(c).exists() and c not in sys.path:
            sys.path.insert(0, c)
            try:
                return importlib.import_module("helada_model")
            except ImportError:
                sys.path.remove(c)
            except Exception as e:  # model present but broken deps: fall back loudly
                log.warning("helada_model found at %s but failed to import: %s", c, e)
                sys.path.remove(c)
    return None


_hm = None if os.environ.get("HELADA_FORCE_STUB") == "1" else _try_import_model()
MODEL_SOURCE = "helada_model" if _hm is not None else "TEMP_STUB"


def _to_model_parcel(p: dict):
    kw = {k: p.get(k) for k in PARCEL_FIELDS if k in p}
    cls = getattr(_hm, "Parcel", None) if _hm is not None else None
    return cls(**kw) if cls else Parcel(**kw)


def _as_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return dict(obj)
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(vars(obj))


# ---------------------------------------------------------------- grid forecast (Open-Meteo)
NIGHT_START_H, NIGHT_END_H = 18, 9   # night of D = 18:00 D .. 08:59 D+1 local
TZ = "America/Mexico_City"

# Offline fallback only: rough monthly mean Tmin for the Toluca valley floor (°C).
_MONTHLY_TMIN = {1: -1.5, 2: -0.5, 3: 1.5, 4: 3.5, 5: 5.5, 6: 7.5, 7: 7.5, 8: 7.3,
                 9: 7.0, 10: 4.5, 11: 1.0, 12: -1.0}


FORECAST_MODEL = "ecmwf_ifs025"   # the model helada_model was trained on (see ../model/README.md)
HOURLY_VARS = ["temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m", "relative_humidity_2m"]


def _unsuffix(hourly: dict, model: str = FORECAST_MODEL) -> dict:
    """Open-Meteo suffixes hourly columns with the model name (`temperature_2m_ecmwf_ifs025`) in some
    responses (several models, some endpoints). Strip it so both layouts read the same."""
    return {k[: -len("_" + model)] if k.endswith("_" + model) else k: v for k, v in hourly.items()}


def _night_from_hourly(h: dict, d0: Date, d1: Date) -> dict:
    idx = [i for i, t in enumerate(h["time"])
           if (t.startswith(d0.isoformat()) and int(t[11:13]) >= NIGHT_START_H)
           or (t.startswith(d1.isoformat()) and int(t[11:13]) < NIGHT_END_H)]
    temps = [h["temperature_2m"][i] for i in idx if h["temperature_2m"][i] is not None]
    if not temps:
        raise ValueError("no hourly data for night window")

    def mean(k):
        v = [h[k][i] for i in idx if h.get(k) and h[k][i] is not None]
        return round(sum(v) / len(v), 2) if v else None
    aft = [h["temperature_2m"][i] for i, t in enumerate(h["time"])
           if t.startswith(d0.isoformat()) and 12 <= int(t[11:13]) <= 17 and h["temperature_2m"][i] is not None]
    wind_kmh = mean("wind_speed_10m")
    return {"grid_tmin_c": round(min(temps), 2), "dew_c": mean("dew_point_2m"), "cloud_cover": mean("cloud_cover"),
            "wind_kmh": wind_kmh, "wind_ms": round(wind_kmh / 3.6, 2) if wind_kmh is not None else None,
            "rh": mean("relative_humidity_2m"), "tmax_prev_c": round(max(aft), 2) if aft else None}


# Grid source when there is neither network nor a saved forecast: a monthly average, not a forecast. Whatever the
# model makes of it is not for a farmer (service.forecast marks the night «unsure»: no alert, no number).
FALLBACK_SOURCE = "offline-climatology-fallback"
SAVED_TAG = "pronóstico guardado"
SAVED_MAX_LEAD_DAYS = 3     # same limit as the phone page: a forecast saved earlier than this before the night is not used


def is_fallback(source: str | None) -> bool:
    return bool(source) and FALLBACK_SOURCE in source


def _saved_key(night_date: str, variant: str | None, parcel_id: str) -> str:
    return f"pred:{night_date}:{variant or '-'}:{parcel_id}"


def _save_prediction(cache, night_date: str, variant: str | None, fcs: list[dict], grid: dict) -> None:
    """Keep the model's answer per parcel and night, so an offline server can repeat it (and say it is saved)."""
    saved_at = datetime.now().astimezone().isoformat(timespec="minutes")
    for f in fcs:
        try:
            key = _saved_key(night_date, variant, f["parcel_id"])
            if ((cache.cache_get(key) or {}).get("forecast")) == f:
                continue        # same answer as last time: no write (on AWS every write is mirrored to DynamoDB)
            cache.cache_put(key, {"forecast": f, "grid": grid.get(f["parcel_id"], {}), "saved_at": saved_at})
        except Exception as e:   # the cache is a convenience: a full disk must not break the forecast
            log.warning("could not save the forecast of %s: %s", f["parcel_id"], e)
            return


def _saved_prediction(cache, parcels: list[dict], night_date: str, variant: str | None) -> dict | None:
    """The saved answer for every parcel asked, or None when one is missing (then nothing saved is used)."""
    fcs, grid = [], {}
    for p in parcels:
        c = cache.cache_get(_saved_key(night_date, variant, p["parcel_id"]))
        if not c or (Date.fromisoformat(night_date) - Date.fromisoformat(c["saved_at"][:10])).days > SAVED_MAX_LEAD_DAYS:
            return None
        fcs.append(c["forecast"])
        grid[p["parcel_id"]] = {**c.get("grid", {}), "source": f"{SAVED_TAG} ({c['saved_at'][:16].replace('T', ' ')})"}
    return {"forecasts": fcs, "grid": grid}


def fetch_grid_forecast(parcels: list[dict], night_date: str, *, offline: bool = False,
                        cache=None) -> dict[str, dict]:
    """Raw grid-cell night forecast per parcel:
    {parcel_id: {grid_tmin_c, dew_c, cloud_cover, wind_kmh, wind_ms, rh, tmax_prev_c, grid_elev_m, source}}.

    Same source as helada_model: Open-Meteo `models=ecmwf_ifs025`, here with elevation=nan (no statistical
    downscaling -> raw grid cell; grid_elev_m tells the model which elevation the numbers refer to).
    Falls back to cache, then to a monthly climatology (flagged) when offline.
    """
    key = f"grid:{FORECAST_MODEL}:" + night_date + ":" + ",".join(p["parcel_id"] for p in parcels)
    d0 = Date.fromisoformat(night_date)
    d1 = d0 + timedelta(days=1)
    today = Date.today()
    if not offline:
        base = ("https://api.open-meteo.com/v1/forecast" if (today - d0).days <= 80
                else "https://historical-forecast-api.open-meteo.com/v1/forecast")
        try:
            r = httpx.get(base, timeout=12, params={
                "latitude": ",".join(f"{p['lat']:.4f}" for p in parcels),
                "longitude": ",".join(f"{p['lon']:.4f}" for p in parcels),
                "elevation": ",".join("nan" for _ in parcels),
                "models": FORECAST_MODEL,
                "hourly": ",".join(HOURLY_VARS),
                "timezone": TZ,           # wind stays in km/h (the model's unit); wind_ms derived
                "start_date": d0.isoformat(), "end_date": d1.isoformat(),
            })
            r.raise_for_status()
            js = r.json()
            js = js if isinstance(js, list) else [js]
            out = {}
            for p, loc in zip(parcels, js):
                night = _night_from_hourly(_unsuffix(loc["hourly"]), d0, d1)
                out[p["parcel_id"]] = {**night, "grid_elev_m": loc.get("elevation"),
                                       "source": f"open-meteo {FORECAST_MODEL}"}
            if cache is not None:
                cache.cache_put(key, out)
            return out
        except Exception as e:  # network down, date out of range, ...
            log.warning("Open-Meteo fetch failed (%s); using cache/fallback", e)
    if cache is not None:
        c = cache.cache_get(key)
        if c:
            for v in c.values():
                v["source"] = f"open-meteo {FORECAST_MODEL} (cache)"
            return c
    base_t = _MONTHLY_TMIN[d0.month]
    return {p["parcel_id"]: {"grid_tmin_c": base_t, "cloud_cover": 30.0, "wind_ms": 2.0, "rh": 70.0,
                             "grid_elev_m": p.get("grid_elev_m"),
                             "source": FALLBACK_SOURCE} for p in parcels}


# ---------------------------------------------------------------- TEMP STUB model
_Z80 = 1.2816  # 80% central interval


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _stub_one(p: dict, night_date: str, g: dict) -> FrostForecast:
    """Backup calculation used only when helada_model cannot run. Physically-motivated guess, no skill claim."""
    grid = float(g["grid_tmin_c"])
    clear = 1 - min(max((g.get("cloud_cover") if g.get("cloud_cover") is not None else 40.0) / 100, 0), 1)
    wind = g.get("wind_ms") if g.get("wind_ms") is not None else 2.0
    calm = max(0.0, 1 - wind / 8.0)
    rad = clear * calm                              # radiative-night strength 0..1
    elev = p.get("elev_m") or 0.0
    gelev = g.get("grid_elev_m") or p.get("grid_elev_m") or elev
    dz = elev - gelev
    lapse = 6.5 * (1 - 0.6 * rad)                   # nocturnal inversions weaken the lapse rate
    lapse_adj = -lapse * dz / 1000
    tpi = p.get("tpi_m", 0.0) or 0.0
    pool_adj = (0.07 * tpi if tpi < 0 else min(0.02 * tpi, 1.0)) * rad
    rad_bias = -1.0 * rad                           # grid cells under-cool on clear calm nights
    tmin = grid + lapse_adj + pool_adj + rad_bias
    sigma = 1.1 + 0.002 * abs(dz) + 0.4 * (1 - rad)
    return FrostForecast(
        parcel_id=p["parcel_id"], date=night_date, grid_tmin_c=round(grid, 1),
        tmin_c=round(tmin, 1), tmin_lo_c=round(tmin - _Z80 * sigma, 1), tmin_hi_c=round(tmin + _Z80 * sigma, 1),
        p_frost=round(_phi((0 - tmin) / sigma), 3),
        drivers={"elev_diff_m": round(dz), "tpi": round(tpi, 1), "clear_sky": round(clear, 2),
                 "wind_ms": round(wind, 1), "lapse_adj_c": round(lapse_adj, 1),
                 "cold_pool_adj_c": round(pool_adj, 1), "radiative_adj_c": round(rad_bias, 1)},
    )


def _stub_climatology(p: dict) -> FrostClimatology:
    """TEMP STUB — elevation/TPI heuristic, NOT a climatology."""
    elev = p.get("elev_m") or 2600
    tpi = p.get("tpi_m", 0.0) or 0.0
    shift = int(round((elev - 2600) / 100 * 6 + max(-tpi, 0) * 0.3))
    last50 = 95 + shift          # ~5 Apr at 2600 m
    first50 = 295 - shift        # ~22 Oct at 2600 m
    return FrostClimatology(p["parcel_id"], last50, last50 + 20, first50 - 15, first50, first50 - last50)


# ---------------------------------------------------------------- public adapter API
LAST_ERROR: str | None = None   # last helada_model failure (then the stub answered)


def effective_source() -> str:
    return "TEMP_STUB" if (_hm is None or LAST_ERROR) else "helada_model"


def _model_failed(where: str, e: Exception) -> None:
    global LAST_ERROR
    LAST_ERROR = f"{where}: {type(e).__name__}: {e}"[:300]
    log.warning("helada_model.%s failed (%s); using TEMP_STUB", where, LAST_ERROR)


HOLDOUT_VARIANT = "holdout_2025_26"
VARIANT_LABELS = {HOLDOUT_VARIANT: "Réplica: modelo entrenado sin la temporada 2025-26"}


def predict(parcels: list[dict], night_date: str, forecast: dict | None = None, *,
            offline: bool = False, cache=None, variant: str | None = None, sites: dict | None = None) -> dict:
    """Returns {"source": ..., "grid": {...}, "forecasts": [FrostForecast as dict, ...]}.

    `night_date` = local EVENING date of the night (18:00 D -> 08:59 D+1), same as helada_model.
    `forecast` (optional) = {parcel_id: {"grid_tmin_c", "cloud_cover", "wind_ms", ...}}. It is
    passed through to helada_model.predict(); the stub uses it instead of fetching Open-Meteo.
    If helada_model raises (e.g. not implemented yet), the stub answers and source says so.
    `variant` (e.g. "holdout_2025_26": trained without the 2025-26 season) selects a helada_model artifact; the
    demo replay of a 2025-26 night uses it so it never shows an in-sample result. Ignored by the stub.
    `sites` = {parcel_id: SiteCalibration dict} from parcel loggers (parcel_logger.py); helada_model then answers
    "logger-anchored (k noches)" for terrain-transfer parcels with readings before the night. Ignored by the stub.
    """
    global LAST_ERROR
    if _hm is not None:
        fetched = forecast is None and not offline       # the model downloads the forecast itself: worth saving
        if forecast is None and offline:
            if cache is not None:
                saved = _saved_prediction(cache, parcels, night_date, variant)
                if saved:           # the last answer computed with a real forecast, flagged as saved
                    return {"source": "helada_model", "variant": variant,
                            "variant_label": VARIANT_LABELS.get(variant), **saved}
            # helada_model would fetch Open-Meteo itself; offline, hand it the cached/fallback grid instead.
            forecast = fetch_grid_forecast(parcels, night_date, offline=True, cache=cache)
        try:
            args = ([_to_model_parcel(p) for p in parcels], night_date, forecast)
            kw = {"variant": variant} if variant else {}
            if sites and getattr(_hm, "SiteCalibration", None):
                kw["sites"] = {pid: _hm.SiteCalibration.from_dict(d) for pid, d in sites.items()}
            res = _hm.predict(*args, **kw)
            fcs = [_as_dict(r) for r in res]
            sup = getattr(_hm, "support_of", None)
            for r, f in zip(res, fcs):
                try:
                    f["support"] = sup(r) if sup else None
                except Exception:
                    f["support"] = None
            grid = {f["parcel_id"]: {"grid_tmin_c": f["grid_tmin_c"],
                                     "source": (forecast or {}).get(f["parcel_id"], {}).get("source", "helada_model")}
                    for f in fcs}
            LAST_ERROR = None
            if fetched and cache is not None:
                _save_prediction(cache, night_date, variant, fcs, grid)
            return {"source": "helada_model", "variant": variant, "variant_label": VARIANT_LABELS.get(variant),
                    "grid": grid, "forecasts": fcs}
        except Exception as e:
            _model_failed("predict", e)
    grid = forecast or fetch_grid_forecast(parcels, night_date, offline=offline, cache=cache)
    fcs = [dataclasses.asdict(_stub_one(p, night_date, grid[p["parcel_id"]])) for p in parcels
           if p["parcel_id"] in grid]
    return {"source": "TEMP_STUB", "variant": None, "variant_label": None, "grid": grid, "forecasts": fcs}


def climatology(parcel: dict) -> dict:
    if _hm is not None:
        try:
            return {"source": "helada_model", **_as_dict(_hm.climatology(_to_model_parcel(parcel)))}
        except Exception as e:
            _model_failed("climatology", e)
    return {"source": "TEMP_STUB", **dataclasses.asdict(_stub_climatology(parcel))}


def support_of_parcel(parcel: dict) -> str | None:
    """'station-anchored' | 'terrain-transfer' for a parcel (no forecast needed). helada_model.support_of accepts a
    Parcel; under the stub, the roster's `near_station` field decides (None when unknown)."""
    if _hm is not None and getattr(_hm, "support_of", None):
        try:
            return _hm.support_of(_to_model_parcel(parcel))
        except Exception as e:
            _model_failed("support_of", e)
    if "near_station" in parcel:
        return "station-anchored" if parcel.get("near_station") else "terrain-transfer"
    return None


def backtest_report() -> dict:
    if _hm is not None:
        try:
            return {"source": "helada_model", **_as_dict(_hm.backtest_report())}
        except Exception as e:
            _model_failed("backtest_report", e)
    out: dict[str, Any] = {
        "source": "TEMP_STUB",
        "model_error": LAST_ERROR,
        "status": "pending",
        "note": "helada_model no está disponible: no hay cifras del modelo que mostrar.",
    }
    return out
