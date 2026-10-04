"""«Registrador en la parcela»: a parcel's own nightly minimum readings -> helada_model site calibration.

Readings come from (a) the officer dashboard (CSV upload or pasted lines «fecha,tmin_c») or (b) the farmer on
WhatsApp («anoche marcó -2», a min-max thermometer read by text or voice), stored only after a yes/no confirmation.
Everything here is deterministic (regex + numbers in code); nothing is inferred by a language model.

Night convention: helada_model uses the EVENING date (night of X = X 18:00 -> X+1 08:00). A min-max thermometer is
read in the morning, so readings dated by the morning (the default for uploads, and «anoche» on WhatsApp) are
stored as the evening before.

The calibration needs the archived day-1 forecast of each logged night (the model's input). Sources, in order:
the demo fixture, the local cache, then the Open-Meteo Previous Runs API (only when online). Nights without a
forecast are listed as skipped, never guessed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from datetime import date as Date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from . import model_adapter
from .config import BACKEND_DIR
from .db import utcnow
from .slots import norm

log = logging.getLogger("helada.logger")

TMIN_RANGE_C = (-25.0, 30.0)          # plausible nightly minimum, 2300-3000 m, central Mexico
PENDING_TTL_H = 12                    # a WhatsApp reading waits this long for its «sí»
DEMO_FILE = BACKEND_DIR / "data" / "demo_logger.json"
DEMO_ID = "R-JOCO"

# Measured learning curve (research/10_logger_learning_curve.py), used when helada_model is not installed.
# k nights -> Tmin MAE (degC) on held-out SMN stations; the model artifact is the source of truth when present.
_FALLBACK_CURVE = [(0, 2.32), (7, 2.12), (14, 1.94), (30, 1.79), (60, 1.56), (90, 1.53), (182, 1.54)]


# ------------------------------------------------------------------ expected precision
def expected_precision(k: int) -> dict:
    hm = model_adapter._hm
    if hm is not None and getattr(hm, "expected_precision", None):
        try:
            return hm.expected_precision(int(k))
        except Exception as e:  # artifact missing in an old install
            log.warning("expected_precision failed: %s", e)
    lv = [x for x in _FALLBACK_CURVE if x[0] <= max(int(k), 0)][-1]
    return {"k": int(k), "level_k": lv[0], "mae_c": lv[1], "halfwidth80_c": None, "recall_at_pofd5": None,
            "ceiling_mae_c": 1.55}


CURVE_KS = (0, 7, 14, 30, 60, 90, 182)


def curve() -> list[dict]:
    """The measured learning curve, for display (k nights -> expected Tmin error)."""
    return [expected_precision(k) for k in CURVE_KS]


# ------------------------------------------------------------------ parsing: tables
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y")
_NUM = r"[-−–]?\d+(?:[.,]\d+)?"


def _parse_date(s: str) -> Date | None:
    s = s.strip().strip('"').split("T")[0].split(" ")[0]
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def _parse_num(s: str) -> float | None:
    s = s.strip().strip('"').replace("−", "-").replace("–", "-").replace("°c", "").replace("°", "").strip()
    if not re.fullmatch(_NUM, s):
        return None
    return float(s.replace(",", "."))


def _fields(line: str) -> list[str]:
    if ";" in line or "\t" in line:
        return [x for x in re.split(r"[;\t]", line)]
    parts = line.split(",")
    if len(parts) == 3 and re.fullmatch(r"\s*[-−–]?\d+\s*", parts[1]) and re.fullmatch(r"\s*\d+\s*", parts[2]):
        return [parts[0], parts[1] + "." + parts[2]]          # 2025-11-15,-1,5 -> decimal comma
    if len(parts) >= 2:
        return parts
    return line.split()


def parse_table(text: str, date_is: str = "morning") -> dict:
    """Lines «fecha,tmin_c» (CSV with or without header; ; or tab also work; decimal comma OK).
    Returns {"rows": [(evening_date_iso, tmin_c)], "errors": [{"line", "text", "reason"}]}."""
    if date_is not in ("morning", "evening"):
        raise ValueError("date_is must be 'morning' or 'evening'")
    rows, errors = [], []
    for i, raw in enumerate(io.StringIO(text or "").read().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        f = _fields(line)
        if len(f) < 2:
            errors.append({"line": i, "text": line[:80], "reason": "se esperaban dos columnas: fecha y temperatura"})
            continue
        d, t = _parse_date(f[0]), _parse_num(f[1])
        if d is None and t is None and i == 1:
            continue                                            # header
        if d is None:
            errors.append({"line": i, "text": line[:80], "reason": "fecha no reconocida (use AAAA-MM-DD o DD/MM/AAAA)"})
            continue
        if t is None:
            errors.append({"line": i, "text": line[:80], "reason": "temperatura no numérica"})
            continue
        if not (TMIN_RANGE_C[0] <= t <= TMIN_RANGE_C[1]):
            errors.append({"line": i, "text": line[:80], "reason": f"fuera de rango ({TMIN_RANGE_C[0]:g} a {TMIN_RANGE_C[1]:g} °C)"})
            continue
        ev = d - timedelta(days=1) if date_is == "morning" else d
        rows.append((ev.isoformat(), round(t, 2)))
    return {"rows": rows, "errors": errors}


# ------------------------------------------------------------------ parsing: WhatsApp reading
_WORDS = {"cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
          "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15}
_WNUM = r"(?:\d+(?:[.,]\d+)?|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")(?: y medio)?"
# a reading needs a reading verb or the thermometer, plus a number; «bajo cero» / «menos» / «-» make it negative
_VERB = (r"(?:marco|marcaba|marca|llego a|llego|bajo a|bajo hasta|bajo|amanecio (?:a|en|con)|amanecimos (?:a|en|con)|"
         r"registro|estuvo en|estuvo a|la minima (?:fue )?(?:de )?|minima de|minima|temperatura (?:minima )?(?:fue )?(?:de )?)")
_READING = re.compile(
    r"(?:\btermometro\b[^0-9]{0,30}?)?\b" + _VERB + r"\s*(?:de\s+|a\s+|en\s+)?(?P<sign>menos\s+|-\s*|−\s*|–\s*)?"
    r"(?P<num>" + _WNUM + r")\s*(?:grados?|°\s*c?|gr)?(?P<below>\s*(?:grados?\s*)?bajo cero)?")
_REL_DAY = re.compile(r"\b(?P<ante>antenoche|anteanoche|antier|antier en la noche)\b|\b(?P<last>anoche|hoy en la manana|"
                      r"en la madrugada|esta manana|esta madrugada|amanecio|amanecimos|hoy temprano)\b")


def _num_value(s: str) -> float:
    s = s.strip()
    half = s.endswith(" y medio")
    s = s.replace(" y medio", "")
    v = float(s.replace(",", ".")) if re.fullmatch(r"\d+(?:[.,]\d+)?", s) else float(_WORDS[s])
    return v + (0.5 if half else 0.0)


def parse_reading(text: str, today: Date) -> dict | None:
    """«anoche marcó -2», «el termómetro marcó menos dos», «amaneció a 3 bajo cero», «la mínima fue de -1.5».
    Returns {"tmin_c", "night_date" (evening, ISO), "day_word"} or None when it is not a thermometer reading."""
    t = norm(text).replace("−", "-").replace("–", "-")
    m = _READING.search(t)
    if not m:
        return None
    if re.search(r"\b(?:hectareas?|has?|surcos?|metros|kilos?|litros?|pesos|dias?)\b", t[m.end():m.end() + 14]):
        return None                                             # «bajó a dos hectáreas» is not a temperature
    v = _num_value(m.group("num"))
    if m.group("sign") or m.group("below"):
        v = -v
    if not (TMIN_RANGE_C[0] <= v <= TMIN_RANGE_C[1]):
        return None
    d = _REL_DAY.search(t)
    back = 2 if (d and d.group("ante")) else 1                  # default: last night
    return {"tmin_c": round(v, 1), "night_date": (today - timedelta(days=back)).isoformat(),
            "day_word": (d.group(0) if d else "anoche")}


# ------------------------------------------------------------------ storage
def ensure_schema(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS logger_obs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      parcel_id TEXT NOT NULL,
      night_date TEXT NOT NULL,          -- EVENING date of the night (helada_model convention)
      tmin_c REAL NOT NULL,
      source TEXT NOT NULL,              -- csv | pegado | whatsapp
      status TEXT NOT NULL,              -- pending | confirmed | rejected
      phone TEXT,
      created_at TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_logger_parcel ON logger_obs(parcel_id, status, night_date)")


def add_readings(svc, parcel_id: str, rows: list[tuple[str, float]], source: str, actor: str = "officer",
                 raw_text: str = "") -> int:
    ensure_schema(svc.db)
    now = utcnow()
    for d, t in rows:
        svc.db.execute("INSERT INTO logger_obs(parcel_id, night_date, tmin_c, source, status, created_at) "
                       "VALUES (?,?,?,?,?,?)", (parcel_id, d, float(t), source, "confirmed", now))
    svc.audit.append(actor, "logger.readings.added", f"parcel:{parcel_id}", {
        "source": source, "n": len(rows), "first_night": min(d for d, _ in rows) if rows else None,
        "last_night": max(d for d, _ in rows) if rows else None,
        "input_sha256": hashlib.sha256(raw_text.encode()).hexdigest() if raw_text else None})
    return len(rows)


def observations(svc, parcel_id: str) -> list[tuple[str, float]]:
    """Confirmed readings, one per night (the latest entry for a night wins)."""
    ensure_schema(svc.db)
    out: dict[str, float] = {}
    for r in svc.db.all("SELECT night_date, tmin_c FROM logger_obs WHERE parcel_id=? AND status='confirmed' "
                        "ORDER BY id", (parcel_id,)):
        out[r["night_date"]] = r["tmin_c"]
    return sorted(out.items())


def pending(svc, phone: str, now: datetime) -> dict | None:
    ensure_schema(svc.db)
    r = svc.db.one("SELECT * FROM logger_obs WHERE phone=? AND status='pending' ORDER BY id DESC LIMIT 1", (phone,))
    if not r:
        return None
    if datetime.fromisoformat(r["created_at"]) < now.astimezone() - timedelta(hours=PENDING_TTL_H):
        svc.db.execute("UPDATE logger_obs SET status='rejected' WHERE id=?", (r["id"],))
        svc.audit.append("system", "logger.reading.expired", f"logger_obs:{r['id']}", {"parcel_id": r["parcel_id"]})
        return None
    return r


def propose(svc, parcel_id: str, phone: str, reading: dict, text: str) -> int:
    ensure_schema(svc.db)
    rid = svc.db.execute("INSERT INTO logger_obs(parcel_id, night_date, tmin_c, source, status, phone, created_at) "
                         "VALUES (?,?,?,?,?,?,?)", (parcel_id, reading["night_date"], reading["tmin_c"], "whatsapp",
                                                    "pending", phone, utcnow()))
    svc.audit.append("farmer", "logger.reading.proposed", f"logger_obs:{rid}", {
        "parcel_id": parcel_id, "night_date": reading["night_date"], "tmin_c": reading["tmin_c"],
        "rule": "parcel_logger.parse_reading", "text_sha256": hashlib.sha256(text.encode()).hexdigest()})
    return rid


def resolve(svc, row: dict, accept: bool) -> None:
    svc.db.execute("UPDATE logger_obs SET status=? WHERE id=?", ("confirmed" if accept else "rejected", row["id"]))
    svc.audit.append("farmer", "logger.reading.confirmed" if accept else "logger.reading.rejected",
                     f"logger_obs:{row['id']}", {"parcel_id": row["parcel_id"], "night_date": row["night_date"],
                                                 "tmin_c": row["tmin_c"]})
    svc.db.execute("DELETE FROM kv_cache WHERE key=?", (f"sitecal:{row['parcel_id']}",))


# ------------------------------------------------------------------ forecasts for logged nights
@lru_cache(maxsize=1)
def demo() -> dict:
    return json.loads(Path(DEMO_FILE).read_text(encoding="utf-8"))


def demo_site() -> dict:
    s = demo()["site"]
    return {"parcel_id": DEMO_ID, "lat": s["lat"], "lon": s["lon"], "owner_name": s["label"],
            "municipality": s["municipality"], "crop": "maiz_temporal", "lang": "es"}


def _arch_key(p: dict, night: str) -> str:
    return f"archive:{model_adapter.FORECAST_MODEL}:{p['lat']:.4f},{p['lon']:.4f}:{night}"


def forecasts_for(svc, parcel: dict, nights: list[str], *, fetch: bool = True) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if parcel["parcel_id"] == DEMO_ID:
        fx = demo()["forecasts"]
        return {d: fx[d] for d in nights if d in fx}
    missing = []
    for d in nights:
        c = svc.db.cache_get(_arch_key(parcel, d))
        if c:
            out[d] = c
        else:
            missing.append(d)
    hm = model_adapter._hm
    if missing and fetch and not svc.s.offline and hm is not None:
        try:
            from helada_model import forecast as hf
            got = hf.fetch_archive(parcel["lat"], parcel["lon"], min(missing), max(missing),
                                   model=model_adapter.FORECAST_MODEL, timeout=60)
            for d in missing:
                if d in got:
                    out[d] = got[d]
                    svc.db.cache_put(_arch_key(parcel, d), got[d])
            svc.audit.append("system", "logger.forecasts.fetched", f"parcel:{parcel['parcel_id']}", {
                "source": "open-meteo previous-runs day-1", "requested": len(missing),
                "got": sum(1 for d in missing if d in got)})
        except Exception as e:
            log.warning("archive fetch failed for %s: %s", parcel["parcel_id"], e)
    return out


# ------------------------------------------------------------------ calibration
def calibrate(svc, parcel: dict, obs: list[tuple[str, float]] | None = None, *, fetch: bool = True,
              variant: str | None = None, store: bool = True) -> dict:
    """helada_model.calibrate_site on the parcel's confirmed readings. Returns a JSON-able summary plus the
    SiteCalibration dict under "site" (None when the model is not installed)."""
    obs = observations(svc, parcel["parcel_id"]) if obs is None else obs
    nights = [d for d, _ in obs]
    fx = forecasts_for(svc, parcel, nights, fetch=fetch)
    hm = model_adapter._hm
    if hm is None or not getattr(hm, "calibrate_site", None):
        return {"parcel_id": parcel["parcel_id"], "n_readings": len(obs), "k": 0, "site": None,
                "note": "helada_model no instalado: se guardan las lecturas, sin calibración",
                "precision": expected_precision(0), "skipped": []}
    sc = hm.calibrate_site(model_adapter._to_model_parcel(parcel), obs, forecasts=fx, variant=variant)
    site = sc.to_dict()
    summ = {"parcel_id": parcel["parcel_id"], "n_readings": len(obs), "k": sc.n_nights, "support": sc.support,
            "first_night": sc.first_night, "last_night": sc.last_night, "a_c": sc.a_c, "b_c": sc.b_c,
            "precision": expected_precision(sc.n_nights), "precision_before": expected_precision(0),
            "skipped": sc.rejected, "site": site}
    if store and parcel["parcel_id"] != DEMO_ID:
        svc.db.cache_put(f"sitecal:{parcel['parcel_id']}", {"obs_sha": _obs_sha(obs), "site": site})
    return summ


def _obs_sha(obs) -> str:
    return hashlib.sha256(json.dumps(obs).encode()).hexdigest()


def site_calibrations(svc, parcels: list[dict]) -> dict[str, dict]:
    """{parcel_id: SiteCalibration dict} for parcels with confirmed readings. Offline-safe: uses cached archive
    forecasts only (the network fetch happens when readings are added)."""
    out = {}
    for p in parcels:
        obs = observations(svc, p["parcel_id"])
        if not obs:
            continue
        c = svc.db.cache_get(f"sitecal:{p['parcel_id']}")
        if c and c.get("obs_sha") == _obs_sha(obs):
            out[p["parcel_id"]] = c["site"]
            continue
        try:
            s = calibrate(svc, p, obs, fetch=False)
            if s.get("site"):
                out[p["parcel_id"]] = s["site"]
        except Exception as e:
            log.warning("site calibration failed for %s: %s", p["parcel_id"], e)
    return out


def summary(svc, parcel: dict) -> dict:
    obs = observations(svc, parcel["parcel_id"])
    c = svc.db.cache_get(f"sitecal:{parcel['parcel_id']}")
    site = c["site"] if c and c.get("obs_sha") == _obs_sha(obs) else None
    k = site["n_nights"] if site else 0
    return {"parcel_id": parcel["parcel_id"], "n_readings": len(obs), "k": k,
            "readings": [{"night_date": d, "tmin_c": t} for d, t in obs[-60:]],
            "precision": expected_precision(k), "precision_before": expected_precision(0),
            "skipped": site["rejected"] if site else [], "curve": curve()}


def band(f: dict) -> dict:
    return {k: f.get(k) for k in ("date", "grid_tmin_c", "tmin_c", "tmin_lo_c", "tmin_hi_c", "p_frost", "support")}


def before_after(svc, parcel_id: str, night_date: str | None = None, scenario: str | None = None) -> dict:
    """The parcel's forecast band for a night without and with its logger calibration (same inputs)."""
    before = svc.forecast(night_date, scenario, parcel_ids=[parcel_id], use_loggers=False)
    after = svc.forecast(night_date, scenario, parcel_ids=[parcel_id])
    b = before["rows"][0]["forecast"] if before["rows"] else None
    a = after["rows"][0]["forecast"] if after["rows"] else None
    return {"date": after["date"], "scenario": after["scenario"], "model_source": after["model_source"],
            "model_variant_label": after.get("model_variant_label"),
            "before": band(b) if b else None, "after": band(a) if a else None,
            "logger_nights_used": int((a or {}).get("drivers", {}).get("logger_nights", 0) or 0)}


def demo_view(until: str | None = None) -> dict:
    """The worked example: a real SMN station the model never saw (E.T.A. 013 Jocotitlán) standing in for a parcel
    logger. Readings of nights before `until` calibrate the site; the band for the night `until` is shown before and
    after, next to what the station actually measured. Precomputed season check from scripts/build_demo_logger.py."""
    D = demo()
    until = until or D["replay_night"]
    obs = [(r["night_date"], r["tmin_c"]) for r in D["observations"] if r["night_date"] < until]
    p = demo_site()
    hm = model_adapter._hm
    truth = next((r["tmin_c"] for r in D["observations"] if r["night_date"] == until), None)
    out = {"site": D["site"], "note": D["note"], "until": until, "k": len(obs), "observed_tmin_c": truth,
           "curve": curve(),
           "season_check": D.get("season_check"), "csv_example": "\n".join(
               ["fecha,tmin_c"] + [f"{(Date.fromisoformat(d) + timedelta(days=1)).isoformat()},{t:g}" for d, t in obs])}
    fx = D["forecasts"].get(until)
    if hm is None or fx is None:
        out.update(before=None, after=None, precision=expected_precision(len(obs)))
        return out
    mp = model_adapter._to_model_parcel(p)
    sc = hm.calibrate_site(mp, obs, forecasts=D["forecasts"], variant=model_adapter.HOLDOUT_VARIANT)
    b = hm.predict([mp], until, forecast=fx, variant=model_adapter.HOLDOUT_VARIANT)[0]
    a = hm.predict([mp], until, forecast=fx, variant=model_adapter.HOLDOUT_VARIANT, sites=[sc])[0]
    fb, fa = model_adapter._as_dict(b), model_adapter._as_dict(a)
    fb["support"], fa["support"] = hm.support_of(b), hm.support_of(a)
    out.update(k=sc.n_nights, before=band(fb), after=band(fa), precision=expected_precision(sc.n_nights),
               precision_before=expected_precision(0), offset_c=fa["drivers"].get("logger_offset_c"))
    return out
