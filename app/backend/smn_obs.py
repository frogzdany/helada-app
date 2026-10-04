"""Observed SMN Tmin for a night, for the loss packet (offline, from a shipped extract).

Data: backend/data/smn_obs_recent.json.gz, built by scripts/build_smn_obs.py from the shared SMN cache
(../data/smn/obs_daily.csv.gz; CONAGUA/SMN daily climatology files). HELADA_SMN_OBS overrides the path.

Convention (same as helada_model): the night of D (evening date) is closed by the 08:00 manual reading dated D+1.
The SMN publishes daily files late, with a lag that depends on the station (days to months). When the reading
is not in the extract yet, the packet says so plainly instead of leaving a silent placeholder.
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import date as Date, timedelta
from functools import lru_cache
from pathlib import Path

from .config import BACKEND_DIR

OBS_FILE = BACKEND_DIR / "data" / "smn_obs_recent.json.gz"


def _path() -> Path:
    return Path(os.environ.get("HELADA_SMN_OBS") or OBS_FILE)


@lru_cache(maxsize=4)
def _load(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def data() -> dict | None:
    return _load(str(_path()))


def reading(station_id: str, obs_date: str) -> float | None:
    """Tmin of the 08:00 reading dated `obs_date` at `station_id`, or None."""
    d = data()
    st = (d or {}).get("stations", {}).get(str(station_id))
    if not st or not st.get("start"):
        return None
    i = (Date.fromisoformat(obs_date) - Date.fromisoformat(st["start"])).days
    if 0 <= i < len(st["tmin"]):
        return st["tmin"][i]
    return None


def lag_text(days: int) -> str:
    if days < 14:
        return f"{max(days, 1)} días"
    if days < 60:
        return f"{round(days / 7)} semanas"
    return f"{round(days / 30.4)} meses"


def observed_for_night(station_id: str, night_date: str) -> dict:
    """Observation for the night that starts on the evening of `night_date`.

    Returns {"status": ok | not_published | missing | no_data, "obs_date", "tmin_c", "prev_tmin_c", "prev_date",
             "last_date", "retrieved", "lag_days", "text"} where `text` is the Spanish line for the packet
    (without the station name; the caller adds it).
    """
    d = data()
    night = Date.fromisoformat(night_date)
    obs_date = (night + timedelta(days=1)).isoformat()
    out = {"station": str(station_id), "obs_date": obs_date, "tmin_c": None, "prev_date": night.isoformat(),
           "prev_tmin_c": None, "last_date": None, "retrieved": (d or {}).get("retrieved"), "lag_days": None}
    if d is None:
        return {**out, "status": "no_data",
                "text": "Sin archivo SMN en este equipo (ejecute scripts/build_smn_obs.py); se agrega al llegar"}
    st = d["stations"].get(str(station_id)) or {}
    last = st.get("last_date")
    out["last_date"] = last
    out["tmin_c"] = reading(station_id, obs_date)
    out["prev_tmin_c"] = reading(station_id, night.isoformat())
    if out["tmin_c"] is not None:
        return {**out, "status": "ok"}
    if last is None or obs_date > last:
        ret = Date.fromisoformat(d["retrieved"])
        lag = (ret - Date.fromisoformat(last)).days if last else None
        out["lag_days"] = lag
        typ = f"rezago típico de esta estación: {lag_text(lag)}" if lag is not None else "sin datos recientes"
        last_txt = f"; último dato publicado: {last}" if last else ""
        return {**out, "status": "not_published",
                "text": f"Observación SMN aún no publicada ({typ}{last_txt}; archivo consultado el {d['retrieved']}). "
                        "Se agrega al llegar."}
    return {**out, "status": "missing",
            "text": f"El SMN no registró lectura el {obs_date} en esta estación (dato faltante en su archivo diario)"}
