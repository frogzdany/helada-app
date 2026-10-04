"""PASACME requirements checklist (Edomex crop-loss support).

Output is ONLY "Documentos completos" or "Falta: X, Y". It never says whether the
farmer qualifies; that is decided by the Secretaría del Campo after an on-site
inspection (Anexo 3 of the 2024 Lineamientos). Rules live in pasacme.yaml.
"""
from __future__ import annotations

import re
from datetime import date as Date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from .alerts import stage_actions
from .config import BACKEND_DIR
from .slots import CAUSE_LABEL, CROP_LABEL

STAGE_LABEL = {"emergencia": "emergencia (recién nacida)", "crecimiento_vegetativo": "crecimiento (antes de espigar)",
               "floracion": "floración (espiga / jilote)", "llenado_de_grano": "llenado de grano (elote)",
               "madurez": "madurez (mazorca seca)"}

RULES_FILE = BACKEND_DIR / "pasacme.yaml"

# Words the packet must never contain (case/accent-insensitive).
FORBIDDEN = re.compile(r"elegib|eligib|\bprocedente|\baprobad|\bcalifica\b|\bderecho a\b|\bse le pagar", re.I)

COMPLETE = "Documentos completos"


@lru_cache(maxsize=1)
def rules() -> dict:
    return yaml.safe_load(Path(RULES_FILE).read_text(encoding="utf-8"))


def _item(key: str, label: str, status: str, detail: str, origin: str) -> dict:
    """status: ok | falta | revisar | info.  origin: padrón | declarado | Helada | pendiente."""
    return {"key": key, "label": label, "status": status, "detail": detail, "origin": origin}


def build_checklist(parcel: dict, report: dict, today: Date, *, prior_packets_this_year: int = 0,
                    weather: dict | None = None) -> dict:
    """report = {slots:{crop,area_ha,cause,date,notes}, photos:[verdict...], preregistro: True|False|"unclear"|None,
    stage_declared: str|None}. preregistro "unclear" = the farmer answered twice without a clear yes/no."""
    R = rules()
    s = report.get("slots") or {}
    photos = report.get("photos") or []
    items: list[dict] = []
    missing: list[str] = []
    confirm: list[str] = []

    # A1 persona física
    if parcel.get("is_individual", True):
        items.append(_item("persona_fisica", "Productor persona física", "ok",
                           parcel.get("owner_name", ""), "padrón"))
    else:
        items.append(_item("persona_fisica", "Productor persona física", "falta",
                           "El padrón no indica persona física", "padrón"))
        missing.append("confirmar productor persona física")

    # A2 municipio / delegación
    deleg = R["delegaciones"].get(parcel.get("municipality", ""), "consultar")
    items.append(_item("delegacion", "Municipio y Delegación Regional", "ok",
                       f"{parcel.get('municipality', '')} — Delegación {deleg}", "padrón"))

    # A3 superficie vs tope
    area = s.get("area_ha")
    max_ha = R["max_ha"]
    if area is None:
        items.append(_item("superficie", f"Superficie dañada (tope {max_ha} ha por productor)", "falta",
                           "No se ha dicho cuánta superficie se dañó", "declarado"))
        missing.append("superficie dañada")
    elif area > max_ha:
        items.append(_item("superficie", f"Superficie dañada (tope {max_ha} ha por productor)", "revisar",
                           f"Declarado {area:g} ha; los lineamientos cubren hasta {max_ha} ha por productor",
                           "declarado"))
    else:
        items.append(_item("superficie", f"Superficie dañada (tope {max_ha} ha por productor)", "ok",
                           f"Declarado {area:g} ha de {parcel.get('area_ha', 0):g} ha de la parcela", "declarado"))

    # A4 cultivo y etapa
    crop = s.get("crop") or parcel.get("crop")
    crop_origin = "declarado" if s.get("crop") else "padrón"
    ev = Date.fromisoformat(s["date"]) if s.get("date") else today
    stage, _ = stage_actions(crop or "_default", ev.month)
    said = report.get("stage_declared")
    said_txt = f"; etapa declarada por el productor: {STAGE_LABEL.get(said, said)}" if said else ""
    items.append(_item("cultivo", "Cultivo y etapa del cultivo", "ok",
                       f"{CROP_LABEL.get(crop, crop)} ({crop_origin}); etapa estimada por calendario: {stage}"
                       f"{said_txt} (la confirma la inspección)", crop_origin))

    # A5 causa y fecha
    cause = s.get("cause")
    if cause is None:
        items.append(_item("causa", "Causa del daño", "falta", "No se ha dicho la causa", "declarado"))
        missing.append("causa del daño")
    elif cause not in R["causes"]:
        items.append(_item("causa", "Causa del daño", "revisar",
                           f"Declarado: {CAUSE_LABEL.get(cause, cause)}. No está en la lista de siniestros "
                           f"agroclimáticos de los lineamientos; consultar con la Delegación", "declarado"))
    else:
        items.append(_item("causa", "Causa del daño", "ok", CAUSE_LABEL.get(cause, cause), "declarado"))
    if not s.get("date"):
        items.append(_item("fecha", "Fecha del evento", "falta", "No se ha dicho la fecha", "declarado"))
        missing.append("fecha del evento")
    else:
        items.append(_item("fecha", "Fecha del evento", "ok", s["date"], "declarado"))

    # A6 aviso en 10 días naturales
    nd = R["notice_days"]
    if s.get("date"):
        elapsed = (today - ev).days
        limit = ev + timedelta(days=nd)
        if elapsed > nd:
            items.append(_item("aviso_10_dias", f"Aviso a la Delegación dentro de {nd} días", "falta",
                               f"Han pasado {elapsed} días desde el evento (límite {limit.isoformat()})", "Helada"))
            missing.append(f"aviso dentro de {nd} días")
        else:
            items.append(_item("aviso_10_dias", f"Aviso a la Delegación dentro de {nd} días", "ok",
                               f"Han pasado {elapsed} día(s). Avisar antes del {limit.isoformat()} "
                               f"(faltan {nd - elapsed} días)", "Helada"))
    else:
        items.append(_item("aviso_10_dias", f"Aviso a la Delegación dentro de {nd} días", "falta",
                           "Sin fecha del evento no se puede calcular el plazo", "Helada"))

    # A7 porcentaje afectado
    pa = parcel.get("area_ha") or 0
    thr = R["min_damage_pct"]
    if area is not None and pa:
        pct = min(100.0, 100 * area / pa)
        st = "ok" if pct >= thr else "revisar"
        items.append(_item("porcentaje", f"Porcentaje de la superficie afectada (umbral {thr}%)", st,
                           f"Declarado {pct:.0f}%; el umbral de los lineamientos es {thr}%; lo verifica la inspección",
                           "declarado"))
    else:
        items.append(_item("porcentaje", f"Porcentaje de la superficie afectada (umbral {thr}%)", "falta",
                           "Falta la superficie dañada", "declarado"))

    # A8 fotos
    need = R["min_photos"]
    n = len(photos)
    okn = sum(1 for p in photos if p.get("status") == "ok")
    detail = f"{n} de {need} fotos; {okn} con fecha y lugar consistentes"
    if n >= need:
        items.append(_item("fotos", f"Fotos del daño (al menos {need})", "ok" if okn == n else "revisar",
                           detail, "Helada"))
    else:
        items.append(_item("fotos", f"Fotos del daño (al menos {need})", "falta", detail, "Helada"))
        missing.append(f"fotos ({n} de {need})")

    # A9 evidencia meteorológica (informativa)
    if cause in ("helada", "nevada") and weather:
        items.append(_item("clima", "Evidencia meteorológica", "info", weather.get("summary", ""), "Helada"))
    else:
        items.append(_item("clima", "Evidencia meteorológica", "info",
                           "Sin evidencia meteorológica automática para esta causa", "Helada"))

    # C1 pre-registro
    pre = report.get("preregistro")
    if pre is True:
        items.append(_item("preregistro", "Pre-registro PASACME (formulario y Delegación)", "ok",
                           "El productor dice que ya lo hizo", "declarado"))
    elif pre == "unclear":
        items.append(_item("preregistro", "Pre-registro PASACME (formulario y Delegación)", "revisar",
                           "La respuesta del productor no fue clara: confirmar con él si ya lo hizo", "declarado"))
        confirm.append("pre-registro PASACME")
    else:
        items.append(_item("preregistro", "Pre-registro PASACME (formulario y Delegación)", "falta",
                           "El productor dice que no lo ha hecho" if pre is False else "Sin confirmar", "declarado"))
        missing.append("pre-registro PASACME")

    # C2 una solicitud por parcela por año
    if prior_packets_this_year:
        items.append(_item("una_por_anio", "Una solicitud por parcela por año", "revisar",
                           f"Ya hay {prior_packets_this_year} paquete(s) de esta parcela este año en Helada",
                           "Helada"))
    else:
        items.append(_item("una_por_anio", "Una solicitud por parcela por año", "ok",
                           "Sin paquetes previos de esta parcela este año en Helada (lo verifica la Delegación)",
                           "Helada"))
    # C3 UGA, C4 adeudos: Helada cannot check them
    items.append(_item("uga", "Parcela fuera de UGA restringida", "info",
                       "Helada no tiene la capa del Ordenamiento Ecológico: verificar con la Delegación", "pendiente"))
    items.append(_item("adeudos", "Sin adeudos con la Secretaría del Campo", "info",
                       "Se confirma en ventanilla", "pendiente"))

    parts = (["Falta: " + ", ".join(missing)] if missing else []) + (["Confirmar: " + ", ".join(confirm)] if confirm else [])
    status = "; ".join(parts) if parts else COMPLETE
    out = {"status": status, "complete": not parts, "missing": missing, "confirm": confirm, "items": items,
           "review": [i["label"] + ": " + i["detail"] for i in items if i["status"] == "revisar"],
           "ventanilla_docs": R["ventanilla_docs"], "rule_9_1": R["rule_9_1"], "process": R["process"],
           "footer": R["footer_lines"], "legend": R["legend_sec14"], "version_note": R["version_note"],
           "delegacion": deleg}
    assert_no_eligibility(out)
    return out


def assert_no_eligibility(obj) -> None:
    """Raises if any string in obj contains eligibility-decision language."""
    def walk(o):
        if isinstance(o, str):
            if FORBIDDEN.search(o) or FORBIDDEN.search(_deaccent(o)):
                raise ValueError(f"forbidden eligibility language in packet text: {o!r}")
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
    walk(obj)


def _deaccent(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
