"""Deterministic alert rule, weekly cap, evening window and Spanish rendering.

The rule (docs/interfaces.md):
  ALERT  if p_frost >= 0.30 (any support regime): text + voice note; max 2 per parcel per rolling 7 days.
  WATCH  if support == terrain-transfer and tmin_lo_c <= 0 and p_frost < 0.30: softer text only (no voice note),
         does not count toward the weekly cap. Also for "logger-anchored" parcels with less than a full season of
         logger nights (LOGGER_FULL_SEASON): in the backtest, frost detection from a partial first season was not
         reliable (model/README.md, "Logger learning curve"), so the safety net stays until then.
Both only go out in the evening window 18:00-20:00 local (otherwise queued), one per parcel per night.
  UNSURE when the model did not run (TEMP_STUB answered) or had no forecast to run on: no alert and no number. One «no estoy seguro, pregunte a
         su técnico» notice per parcel per night inside the window, never queued, with its own weekly cap.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date as Date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from .config import BACKEND_DIR

P_FROST_MIN = 0.3
TMIN_LO_MAX = 0.0          # WATCH only (terrain-transfer parcels); no longer an alert trigger
MAX_PER_7D = 2
MAX_UNSURE_PER_7D = 2      # «no estoy seguro» notices (model down), counted apart from alerts
LOGGER_FULL_SEASON = 150   # logger nights after which a logger-anchored parcel drops the WATCH safety net
WINDOW_START = time(18, 0)
WINDOW_END = time(20, 0)

ADVISORY_FILE = BACKEND_DIR / "advisory.yaml"


@dataclass
class Decision:
    action: str          # send | queue | skip
    reason: str          # riesgo | vigilancia | fuera_de_ventana | sin_riesgo | tope_semanal | ya_avisado | baja
    triggered: bool
    level: str | None = None   # alert | watch | None

    def as_dict(self) -> dict:
        return {"action": self.action, "reason": self.reason, "triggered": self.triggered, "level": self.level}


def level(fc: dict) -> str | None:
    """'alert' | 'watch' | None (see module docstring)."""
    if fc["p_frost"] >= P_FROST_MIN:
        return "alert"
    if _watch_regime(fc) and fc["tmin_lo_c"] <= TMIN_LO_MAX:
        return "watch"
    return None


def _watch_regime(fc: dict) -> bool:
    s = support(fc) or ""
    if s == "terrain-transfer":
        return True
    return s.startswith("logger-anchored") and (fc.get("drivers") or {}).get("logger_nights", 0) < LOGGER_FULL_SEASON


def triggers(fc: dict) -> bool:
    """True only for a full ALERT (a WATCH is not an alert)."""
    return level(fc) == "alert"


def in_window(now_local: datetime) -> bool:
    return WINDOW_START <= now_local.time() < WINDOW_END


def count_in_last_7d(history: list[datetime], now: datetime) -> int:
    lo = now - timedelta(days=7)
    return sum(1 for t in history if lo < t <= now)


def decide(fc: dict, history: list[datetime], now_local: datetime, *, already_for_date: bool = False,
           opted_out: bool = False, override_window: bool = False) -> Decision:
    """Pure function. `history` = local datetimes of ALERTS (not watches) already sent/queued for this parcel."""
    lv = level(fc)
    if lv is None:
        return Decision("skip", "sin_riesgo", False)
    if opted_out:
        return Decision("skip", "baja", True, lv)
    if already_for_date:
        return Decision("skip", "ya_avisado", True, lv)
    if lv == "alert" and count_in_last_7d(history, now_local) >= MAX_PER_7D:
        return Decision("skip", "tope_semanal", True, lv)
    if not override_window and not in_window(now_local):
        return Decision("queue", "fuera_de_ventana", True, lv)
    return Decision("send", "riesgo" if lv == "alert" else "vigilancia", True, lv)


# ------------------------------------------------------------------ rendering
@lru_cache(maxsize=1)
def advisory() -> dict:
    return yaml.safe_load(Path(ADVISORY_FILE).read_text(encoding="utf-8"))


def advisory_signed() -> bool:
    return bool((advisory().get("meta") or {}).get("signed_by"))


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_larga(d: Date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def noche(d: Date) -> str:
    d1 = d + timedelta(days=1)
    if d.month == d1.month:
        return f"{DIAS[d.weekday()]} {d.day} al {DIAS[d1.weekday()]} {d1.day} de {MESES[d.month - 1]}"
    return f"{fecha_larga(d)} al {fecha_larga(d1)}"


def temp_voz(t: float) -> str:
    n = round(t)
    if n == 0:
        return "cero grados"
    if abs(n) == 1:
        return "un grado bajo cero" if n < 0 else "un grado"
    return f"{abs(n)} grados bajo cero" if n < 0 else f"{n} grados"


def temp_txt(t: float) -> str:
    return f"{t:+.1f} °C".replace("+-", "−").replace("-", "−")


def prob_voz(p: float) -> str:
    n = min(10, round(p * 10))
    return f"{n} de cada 10" if n else "menos de 1 de cada 10"     # never «1 de cada 10» for a night that is not cold


def prob_sms(p: float) -> str:
    n = min(10, round(p * 10))
    return f"{n}/10" if n else "<1/10"


def trato(parcel: dict) -> str:
    """How the farmer is addressed: «don Aurelio», «doña Teresa». The form of address is the roster field `trato`;
    it is never guessed from the name. Without it, the first name alone."""
    first = (parcel.get("owner_name") or "").split(" (")[0].split(" ")[0]
    if not first:
        return "productor"
    t = (parcel.get("trato") or "").strip()
    return f"{t} {first}" if t else first


def ascii_sms(s: str) -> str:
    s = s.replace("ñ", "n").replace("Ñ", "N")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def first_sentence(txt: str, limit: int = 110) -> str:
    """First sentence of an advisory action (short alert). Falls back to a word-boundary cut."""
    m = re.match(r"(.+?[.!?])(\s|$)", txt.strip())
    s = m.group(1) if m else txt.strip()
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return s


def stage_actions(crop: str, month: int) -> tuple[str, list[dict]]:
    crops = advisory()["crops"]
    c = crops.get(crop) or crops["_default"]
    for st in c["stages"]:
        if month in st["months"]:
            return st["stage"], st["actions"]
    return "cualquier etapa", crops["_default"]["stages"][0]["actions"]


def support(fc: dict) -> str | None:
    """'station-anchored' | 'terrain-transfer' | 'logger-anchored (k noches)' | None (no regime, e.g. TEMP_STUB)."""
    s = fc.get("support")
    if s:
        return s
    d = fc.get("drivers") or {}
    if "support_station_anchored" in d:
        return "station-anchored" if d["support_station_anchored"] else "terrain-transfer"
    return None


def station_name(fc: dict) -> str:
    from .roster import STATIONS
    sid = (fc.get("drivers") or {}).get("nearest_station_id")
    if sid is None:
        return "cercana"
    sid = str(int(sid))
    return next((s[1] for s in STATIONS if s[0] == sid), f"n.º {sid}")


def support_lines(fc: dict) -> dict:
    t = advisory()["templates"].get("support") or {}
    s = support(fc)
    d = fc.get("drivers") or {}
    if s == "station-anchored":
        km = f"{d.get('nearest_station_km', 0):.1f}"
        return {"soporte_voz": t.get("station_voz", ""),
                "soporte_txt": t.get("station_txt", "").format(estacion=station_name(fc), km=km),
                "aprox_sms": "", "aprox_txt": "", "aprox_voz": ""}
    if s and s.startswith("logger-anchored"):
        k = int(d.get("logger_nights") or 0)
        mae = d.get("logger_expected_mae_c") or 0
        return {"soporte_voz": t.get("logger_voz", "").format(k=k),
                "soporte_txt": t.get("logger_txt", "").format(k=k, mae=f"{mae:.1f}"),
                "aprox_sms": "", "aprox_txt": "", "aprox_voz": ""}
    if s == "terrain-transfer":
        # The short alert keeps the honesty marker; the full caveat is in the detail message.
        return {"soporte_voz": t.get("transfer_voz", ""), "soporte_txt": t.get("transfer_txt", ""),
                "aprox_sms": " aprox", "aprox_txt": ", cálculo aproximado: sin estación cercana",
                "aprox_voz": ", cálculo aproximado porque no hay estación cerca"}
    return {"soporte_voz": "", "soporte_txt": "", "aprox_sms": "", "aprox_txt": "", "aprox_voz": ""}


def porque(fc: dict) -> str:
    d = fc.get("drivers") or {}
    bits = []
    sup = support(fc)
    if sup == "station-anchored" and fc["tmin_c"] <= fc["grid_tmin_c"] - 1:
        return "queda más fría: la estación de junto registra noches más frías que el pronóstico"
    if sup and sup.startswith("logger-anchored"):
        if fc["tmin_c"] <= fc["grid_tmin_c"] - 0.5:
            return "el termómetro de su parcela muestra que ahí hace más frío de lo que dice ese pronóstico"
        return "se ajustó con las lecturas del termómetro de su parcela"
    if sup == "terrain-transfer":   # no terrain story here: terrain did not explain station offsets (model README)
        if fc["tmin_c"] <= fc["grid_tmin_c"] - 0.5:
            return "suele quedar más fría de lo que dice ese pronóstico"
        return "puede quedar distinta a lo que dice ese pronóstico"
    if (d.get("tpi") or 0) <= -5:
        bits.append("está en una parte baja donde se junta el aire frío")
    if (d.get("elev_diff_m") or 0) >= 50:
        bits.append(f"está {int(d['elev_diff_m'])} metros más alta que el promedio de la zona")
    if not bits:
        if fc["tmin_c"] < fc["grid_tmin_c"]:
            bits.append("se enfría más que el promedio de la zona")
        else:
            bits.append("tiene su propio clima")
    return " y ".join(bits)


def render_watch(parcel: dict, fc: dict, now_local: datetime | None = None) -> dict:
    """WATCH message (terrain-transfer, cold end of the band <= 0, P < 30%): text + SMS, no voice note."""
    t = advisory()["templates"]["watch"]
    d = Date.fromisoformat(fc["date"])
    fields = dict(parcel_id=parcel["parcel_id"], municipio=parcel.get("municipality", ""), noche=noche(d),
                  tmin_txt=temp_txt(fc["tmin_c"]), lo_txt=temp_txt(fc["tmin_lo_c"]), hi_txt=temp_txt(fc["tmin_hi_c"]),
                  prob_voz=prob_voz(fc["p_frost"]), noche_corta=f"{d.day}-{(d + timedelta(days=1)).day}/{d.month}",
                  tmin_int=round(fc["tmin_c"]), lo_int=round(fc["tmin_lo_c"]),
                  prob_n=prob_sms(fc["p_frost"]))
    text = "\n".join(line.format(**fields) for line in t["text"])
    if not advisory_signed():
        text += "\n(" + advisory()["meta"].get("unsigned_label", "Borrador sin firma agronómica") + ")"
    sms = ascii_sms(t["sms"].format(**fields))[:160]
    return {"voice": None, "text": text, "sms": sms, "stage": "vigilancia", "sources": []}


def tecnico() -> dict:
    """The person «pregunte a su técnico» points to (advisory.yaml `contact`), as template fields: {tecnico} for
    text, {tecnico_voz} digit by digit for the voice note, {tecnico_sms} digits only. A fictional contact says so."""
    c = advisory().get("contact") or {}
    phone = str(c.get("phone", "")).strip()
    if not phone:
        return {"tecnico": "en la oficina de la Delegación", "tecnico_voz": "de la oficina", "tecnico_sms": "Delegacion"}
    fict = bool(c.get("fictional"))
    return {"tecnico": f"tel. {phone}" + (" (número ficticio de la demostración)" if fict else ""),
            "tecnico_voz": ", ".join(" ".join(g) for g in phone.split()) + (", que es un número de demostración" if fict else ""),
            "tecnico_sms": re.sub(r"\D", "", phone)}


def render_unsure(parcel: dict, now_local: datetime | None = None) -> dict:
    """«No estoy seguro» notice for a night with no validated number (the model did not run, or had no forecast).
    Carries no temperature and no probability; names the person to ask and their phone."""
    t = advisory()["templates"]["unsure"]
    now_local = now_local or datetime.now()
    fields = dict(parcel_id=parcel["parcel_id"], municipio=parcel.get("municipality", ""),
                  saludo="Buenas tardes" if now_local.hour < 19 else "Buenas noches", **tecnico())
    text = "\n".join(line.format(**fields) for line in t["text"])
    if not advisory_signed():
        text += "\n(" + advisory()["meta"].get("unsigned_label", "Borrador sin firma agronómica") + ")"
    return {"voice": t["voice"].format(**fields), "text": text, "sms": ascii_sms(t["sms"].format(**fields))[:160],
            "stage": "sin_modelo", "sources": []}


def render(parcel: dict, fc: dict, now_local: datetime | None = None) -> dict:
    """Returns {"voice": str, "text": str, "sms": str, "stage": str, "sources": [...]}"""
    adv = advisory()
    t = adv["templates"]
    d = Date.fromisoformat(fc["date"])
    now_local = now_local or datetime.now()
    stage, actions = stage_actions(parcel.get("crop", "maiz_temporal"), d.month)
    acts = actions[:2]
    saludo = "Buenas tardes" if now_local.hour < 19 else "Buenas noches"
    acciones = " ".join(f"{'Primero' if i == 0 else 'Segundo'}: {a['text']}" for i, a in enumerate(acts))
    acciones_txt = "\n".join(f"{i + 1}) {a['text']}" for i, a in enumerate(acts))
    fields = dict(
        saludo=saludo, trato=trato(parcel), municipio=parcel.get("municipality", ""),
        noche=noche(d), tmin_voz=temp_voz(fc["tmin_c"]), grid_voz=temp_voz(fc["grid_tmin_c"]),
        porque=porque(fc), prob_voz=prob_voz(fc["p_frost"]), acciones=acciones,
        parcel_id=parcel["parcel_id"], tmin_txt=temp_txt(fc["tmin_c"]), lo_txt=temp_txt(fc["tmin_lo_c"]),
        hi_txt=temp_txt(fc["tmin_hi_c"]), grid_txt=temp_txt(fc["grid_tmin_c"]), acciones_txt=acciones_txt,
        noche_corta=f"{d.day}-{(d + timedelta(days=1)).day}/{d.month}", tmin_int=round(fc["tmin_c"]),
        grid_int=round(fc["grid_tmin_c"]), prob_n=prob_sms(fc["p_frost"]),
        accion_sms=ascii_sms(acts[0].get("sms", "")) if acts else "",
        prob_pct=round(fc["p_frost"] * 100),
        accion_corta=(first_sentence(acts[0]["text"]) if acts else "Proteja sus cultivos esta noche."),
        accion_voz=(first_sentence(acts[0]["text"]) if acts else ""),
        borrador="" if advisory_signed() else " (" + adv["meta"].get("unsigned_label", "Borrador sin firma agronómica") + ")",
        **support_lines(fc),
    )
    voice = " ".join(t["alert_voice"].format(**fields).split())
    text = "\n".join(ln.strip() for ln in t["alert_text"].format(**fields).strip().split("\n") if ln.strip())
    detail = t["alert_detail"].format(**fields).strip()
    # folded YAML joins lines with spaces; restore line breaks for chat readability
    for marker in ["Noche del", "Su parcela:", "Pronóstico general", "Base:", "Probabilidad", "1)", "2)",
                   "Si hay daño:", "BAJA ="]:
        detail = detail.replace(" " + marker, "\n" + marker)
    detail = "\n".join(line for line in detail.split("\n") if line.strip())
    if not advisory_signed():
        detail += "\n(" + adv["meta"].get("unsigned_label", "Borrador sin firma agronómica") + ")"
    sms = ascii_sms(t["alert_sms"].format(**fields))
    if len(sms) > 160:          # keep "BAJA=salir": drop the action hint before truncating
        sms = ascii_sms(t["alert_sms"].format(**{**fields, "accion_sms": "Ver audio"}))
    if len(sms) > 160:
        sms = sms[:157] + "..."
    return {"voice": voice, "text": text, "detail": detail, "sms": sms, "stage": stage,
            "sources": [a.get("source") for a in acts]}
