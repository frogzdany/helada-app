"""«¿Cuándo siembro?»: planting-date and cycle advisor for rainfed maize, per parcel. Deterministic, no LLM.

Inputs:
  * the parcel's frost climatology from helada_model.climatology() (SMN 1991-2025; shelter Tmin <= 0 °C;
    last spring frost p50/p90, first autumn frost p10/p50). Terrain-transfer parcels (no station within 1.5 km)
    use the EARLIER first-frost / LATER last-frost of (their terrain estimate, the nearest SMN station), because
    the transfer estimate is modelled, not validated (model/README.md);
  * the variety-cycle table in advisory.yaml `planting` (INIFAP/ICAMEX sources, altitude-scaled).

Rule (advisory.yaml): a cycle "fits" a sowing date if physiological maturity (sowing + maturity days + margin)
falls before the first-autumn-frost p10 (risk-averse: 1 year in 10 an earlier frost) or p50 (1 in 2).
Frost risk at flowering / before maturity for a given sowing date uses a normal distribution fitted to the
p10/p50 first-frost dates (approximation; stated in the output).

Honest limits: rainfed sowing depends on when the rains arrive. This advice is for choosing the cycle / variety
and the date within the moisture window, not a promise of harvest. The table REQUIRES an agronomist's signature.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import date as Date, timedelta
from pathlib import Path

from . import alerts as A
from . import model_adapter, roster

REF_YEAR = 2026            # non-leap year for day-of-year <-> date display
MESES_CORTO = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
Z90 = 1.2816               # p50 - p10 = 1.2816 sigma for a normal distribution


# ------------------------------------------------------------------ config / helpers
def cfg() -> dict:
    return A.advisory()["planting"]["maiz_temporal"]


def sources() -> dict:
    adv = A.advisory()
    return {**adv.get("sources", {}), **adv.get("sources_planting", {})}


def doy_of(mmdd: str) -> int:
    m, d = (int(x) for x in mmdd.split("-"))
    return Date(REF_YEAR, m, d).timetuple().tm_yday


def date_of(doy: int) -> Date:
    return Date(REF_YEAR, 1, 1) + timedelta(days=int(doy) - 1)


def fecha(doy: int) -> str:
    d = date_of(doy)
    return f"{d.day} {MESES_CORTO[d.month - 1]}"


def fecha_larga(doy: int) -> str:
    d = date_of(doy)
    return f"{d.day} de {A.MESES[d.month - 1]}"


def mmdd(doy: int) -> str:
    return date_of(doy).strftime("%m-%d")


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def de_cada_10(p: float) -> str:
    n = round(p * 10)
    if n <= 0:
        return "menos de 1 de cada 10"
    return f"{min(n, 10)} de cada 10"


def cycle_days(cycle: dict, elev_m: float | None, pct_per_100m: float) -> tuple[int, int]:
    """(days to flowering, days to physiological maturity) at the parcel's altitude."""
    elev = elev_m if elev_m is not None else cycle["ref_elev_m"]
    f = max(0.8, 1 + pct_per_100m / 100 * (elev - cycle["ref_elev_m"]) / 100)
    return round(cycle["flowering_days"] * f), round(cycle["maturity_days"] * f)


# ------------------------------------------------------------------ climatology basis
def _clim_basis(parcel: dict, clim: dict | None = None) -> dict:
    """Frost-date percentiles used for the advice, and the plain-language basis."""
    c = clim or model_adapter.climatology(parcel)
    ns = roster.nearest_station(parcel["lat"], parcel["lon"])
    sup = model_adapter.support_of_parcel(parcel) if c.get("source") != "TEMP_STUB" else None
    vals = {k: int(c[k]) for k in ("last_spring_frost_doy_p50", "last_spring_frost_doy_p90",
                                   "first_autumn_frost_doy_p10", "first_autumn_frost_doy_p50")}
    estimates = [{"what": "parcela", **vals, "source": c.get("source")}]
    st_name = f"{ns['name']}, n.º {ns['station']},"
    if c.get("source") == "TEMP_STUB":
        basis = "STUB temporal: climatología aproximada por altitud, sin validación"
    elif sup == "station-anchored":
        basis = f"estación SMN {st_name} a {ns['distance_km']} km de su parcela; registros 1991-2025"
    else:
        basis = (f"sin estación a menos de 1.5 km: se usa la fecha más temprana entre la estimación por terreno "
                 f"y la estación SMN {st_name} a {ns['distance_km']} km (aproximado)")
        try:
            sc = model_adapter.climatology({"parcel_id": f"SMN{ns['station']}", "lat": ns["lat"], "lon": ns["lon"]})
            if sc.get("source") != "TEMP_STUB":
                s_vals = {k: int(sc[k]) for k in vals}
                estimates.append({"what": f"estación SMN {st_name}", **s_vals, "source": sc.get("source")})
                vals = {"last_spring_frost_doy_p50": max(vals["last_spring_frost_doy_p50"], s_vals["last_spring_frost_doy_p50"]),
                        "last_spring_frost_doy_p90": max(vals["last_spring_frost_doy_p90"], s_vals["last_spring_frost_doy_p90"]),
                        "first_autumn_frost_doy_p10": min(vals["first_autumn_frost_doy_p10"], s_vals["first_autumn_frost_doy_p10"]),
                        "first_autumn_frost_doy_p50": min(vals["first_autumn_frost_doy_p50"], s_vals["first_autumn_frost_doy_p50"])}
        except Exception:   # nearest-station lookup is a refinement; the parcel estimate stands alone
            pass
    return {"support": sup, "basis": basis, "station": ns, "source": c.get("source"), "used": vals,
            "estimates": estimates}


# ------------------------------------------------------------------ core computation
def compute(clim: dict, elev_m: float | None, conf: dict | None = None) -> dict:
    """Pure function: frost-date percentiles (DOY) + altitude -> windows, risk table, recommendation."""
    conf = conf or cfg()
    ls50, ls90 = clim["last_spring_frost_doy_p50"], clim["last_spring_frost_doy_p90"]
    fa10, fa50 = clim["first_autumn_frost_doy_p10"], clim["first_autumn_frost_doy_p50"]
    sigma = max((fa50 - fa10) / Z90, 5.0)
    p_by = lambda doy: _phi((doy - fa50) / sigma)          # P(first autumn frost on or before doy)
    margin = conf.get("margin_days", 0)
    fw = conf.get("flowering_window_days", 10)
    cycles = []
    for cy in conf["cycles"]:
        fd, md = cycle_days(cy, elev_m, conf["altitude_pct_per_100m"])
        cycles.append({"id": cy["id"], "label": cy["label"], "short": cy.get("short", cy["id"]),
                       "examples": cy.get("examples", ""), "flowering_days": fd, "maturity_days": md,
                       "verify": bool(cy.get("verify")), "src": cy.get("src", []), "note": cy.get("note"),
                       "latest_p10_doy": fa10 - md - margin, "latest_p50_doy": fa50 - md - margin})
    for c in cycles:
        c["latest_p10"], c["latest_p50"] = mmdd(c["latest_p10_doy"]), mmdd(c["latest_p50_doy"])
        c["latest_p10_txt"], c["latest_p50_txt"] = fecha(c["latest_p10_doy"]), fecha(c["latest_p50_doy"])

    def cell(c: dict, sow: int) -> dict:
        fl, ma = sow + c["flowering_days"], sow + c["maturity_days"]
        return {"cycle": c["id"], "flowering": mmdd(fl), "flowering_txt": fecha(fl), "maturity": mmdd(ma),
                "maturity_txt": fecha(ma), "p_frost_flowering": round(p_by(fl + fw), 3),
                "p_frost_before_maturity": round(p_by(ma + margin), 3)}

    table = [{"sow": d, "sow_txt": fecha(doy_of(d)), "cells": [cell(c, doy_of(d)) for c in cycles]}
             for d in conf["table_dates"]]
    rec = {}
    by_len = sorted(cycles, key=lambda c: c["maturity_days"])
    for key, dd in conf["decision_dates"].items():
        sow = doy_of(dd["date"])
        fit = [c for c in by_len if c["latest_p10_doy"] >= sow]
        r = {"date": dd["date"], "date_txt": fecha(sow), "label": dd["label"], "src": dd.get("src")}
        if fit:
            best = fit[-1]
            longer = [c for c in by_len if c["maturity_days"] > best["maturity_days"]]
            alt = longer[0] if longer and longer[0]["latest_p10_doy"] >= doy_of("03-01") else None
            r.update(status="ok", cycle=best["id"], cycle_label=best["label"], latest=best["latest_p10"],
                     latest_txt=best["latest_p10_txt"],
                     alt=({"cycle": alt["id"], "cycle_label": alt["label"], "latest_txt": alt["latest_p10_txt"]}
                          if alt else None))
        else:
            c0 = by_len[0]
            ce = cell(c0, sow)
            r.update(status="none", cycle=c0["id"], cycle_label=c0["label"],
                     p_frost_before_maturity=ce["p_frost_before_maturity"],
                     p_frost_flowering=ce["p_frost_flowering"])
        rec[key] = r
    return {"frost": {"last_spring_p50": mmdd(ls50), "last_spring_p90": mmdd(ls90), "first_autumn_p10": mmdd(fa10),
                      "first_autumn_p50": mmdd(fa50), "last_spring_p50_txt": fecha(ls50),
                      "last_spring_p90_txt": fecha(ls90), "first_autumn_p10_txt": fecha(fa10),
                      "first_autumn_p50_txt": fecha(fa50), "frost_free_days": fa50 - ls50,
                      "sigma_days": round(sigma, 1)},
            "emergence_after_last_frost_from": mmdd(ls90 - conf.get("emergence_days", 12)),
            "cycles": cycles, "table": table, "recommendation": rec}


# ------------------------------------------------------------------ rendering
def _trato(parcel: dict) -> str:
    t = A.trato(parcel)
    return t[0].upper() + t[1:]


def cname(label: str) -> str:
    """'Ciclo corto (precoz)' -> 'ciclo corto'."""
    return label.split(" (")[0].lower()


def _approx(p: float) -> str:
    if p < 0.05:
        return "menos de 1 de cada 10"
    return "casi todos los" if p >= 0.95 else f"unos {de_cada_10(p)}"


def _img_risk(p: float) -> str:
    # short on purpose: the worst cells must not be the ones in the smallest type
    return "<1 de cada 10" if p < 0.05 else "casi siempre" if p >= 0.95 else de_cada_10(p)


def _rec_line(r: dict, cyc_by_id: dict) -> str:
    if r["status"] == "ok":
        s = f"➜ Si siembra {r['label']}: {cname(r['cycle_label'])}, a más tardar el {r['latest_txt']}"
        if r.get("alt"):
            s += f"; el {cname(r['alt']['cycle_label'])} solo si siembra antes del {r['alt']['latest_txt']}"
        return s + "."
    return (f"➜ Si siembra {r['label']}: ningún ciclo madura seguro antes de la helada temprana. Con "
            f"{cname(r['cycle_label'])}, la helada llegaría antes de que madure {_approx(r['p_frost_before_maturity'])} "
            f"años (en plena floración: {_approx(r['p_frost_flowering'])}). "
            f"Pregunte a su técnico por la variedad más precoz.")


def _farmer_text(parcel: dict, adv: dict) -> str:
    """The chat answer, for a farmer with primary school or less: one recommendation for each way of sowing, with
    its warning next to it. The full table (three cycles, seven dates) is the image sent with it and the
    «Calendario de siembra» tab of the dashboard; the basis and the sources stay there too."""
    fr = adv["frost"]
    cyc = {c["id"]: c for c in adv["cycles"]}
    rr, rt = adv["recommendation"].get("humedad_residual"), adv["recommendation"].get("temporal")

    def maiz(r: dict) -> str:
        return f"maíz de {cname(r['cycle_label'])} ({cyc[r['cycle']]['examples']})"
    lines = [f"🌱 Siembra de maíz · parcela {parcel['parcel_id']} ({parcel.get('municipality', '')})"]
    if parcel.get("crop") and parcel["crop"] != "maiz_temporal":
        lines.append("(Por ahora el calendario es solo para maíz de temporal.)")
    n = 0
    if rr:
        n += 1
        safe_from = _spring_safe_from(adv)       # sowing early has its own risk: the last spring frost
        if rr["status"] != "ok":
            lines.append(f"{n}) Si siembra en abril, con humedad o riego: ningún maíz madura seguro antes de la "
                         "primera helada.")
        elif safe_from and safe_from <= doy_of(rr["latest"]):
            lines.append(f"{n}) Si siembra en abril, con humedad o riego: use {maiz(rr)} y siembre entre el "
                         f"{fecha_larga(safe_from)} y el {fecha_larga(doy_of(rr['latest']))}. Así nace después de "
                         "la última helada y madura antes de la primera, 9 de cada 10 años.")
        else:
            lines.append(f"{n}) Si siembra en abril, con humedad o riego: use {maiz(rr)} y siembre a más tardar el "
                         f"{fecha_larga(doy_of(rr['latest']))}. Así madura antes de la primera helada 9 de cada 10 años.")
            if safe_from:
                lines.append(f"Ojo: en su parcela todavía puede helar hasta el "
                             f"{fecha_larga(doy_of(fr['last_spring_p90']))}, cuando la planta ya nació. "
                             "Si siembra temprano, arriesga la helada de primavera; si siembra tarde, la de otoño.")
    if rt:
        n += 1
        if rt["status"] == "ok":
            lines.append(f"{n}) Si espera las lluvias de junio: use {maiz(rt)}.")
        else:
            lines.append(f"{n}) Si espera las lluvias de junio: aun con {maiz(rt)}, "
                         f"{_approx(rt['p_frost_before_maturity'])} años la helada llega antes de que el maíz madure "
                         "y se pierde parte del grano.")
    lines.append("Pregunte a su técnico qué variedad le conviene. Las fechas cambian según lleguen las lluvias; "
                 "esto no garantiza la cosecha.")
    if not adv["signed"]:
        lines.append("Esto todavía no lo revisó un agrónomo.")
    return "\n".join(lines)


def _spring_safe_from(adv: dict) -> int | None:
    """Day of year from which a sowing emerges after the last spring frost (9 years in 10), when that is later than
    the mid-April sowing the advice is about. None when mid-April is already past that risk."""
    rr = adv["recommendation"].get("humedad_residual")
    safe = doy_of(adv["emergence_after_last_frost_from"])
    return safe if rr and safe > doy_of(rr["date"]) else None


def _farmer_sms(parcel: dict, adv: dict) -> str:
    """One SMS segment (<= 160, GSM-7). The «not reviewed» mark goes first so a cut never drops it, and nothing
    depends on a letter that the ASCII form changes («años» -> «anos»)."""
    fr = adv["frost"]
    rr, rt = adv["recommendation"].get("humedad_residual"), adv["recommendation"].get("temporal")
    head = f"SIEMBRA {parcel['parcel_id']}" + ("" if adv["signed"] else " (sin revisar)") + ": "
    bits = []
    if rr:
        bits.append(f"maiz {cname(rr['cycle_label'])}, siembre hasta el {rr['latest_txt']}." if rr["status"] == "ok"
                    else "en abril ningun maiz madura seguro.")
        if _spring_safe_from(adv):
            bits.append(f"Puede helar hasta {fr['last_spring_p90_txt']}.")
    if rt:
        bits.append(f"Con lluvias de junio: maiz {cname(rt['cycle_label'])}." if rt["status"] == "ok"
                    else f"Si siembra en junio, {max(1, round(rt['p_frost_before_maturity'] * 10))} de 10 veces se hiela.")
    full = A.ascii_sms(head + " ".join(bits))
    # shorter wordings, tried in order, instead of cutting the end off
    for tail, cuts in (("Pregunte a su tecnico.", ()), ("Vea a su tecnico.", ()),
                       ("Vea a su tecnico.", ((", siembre hasta el", ", hasta el"),)),
                       ("Vea a su tecnico.", ((", siembre hasta el", ", hasta el"), ("Puede helar hasta", "Hiela hasta")))):
        sms = full
        for a, b in cuts:
            sms = sms.replace(a, b)
        sms = f"{sms} {tail}"
        if len(sms) <= 160:
            return sms
    return sms[:160]


def render(parcel: dict, adv: dict) -> dict:
    conf = cfg()
    fr = adv["frost"]
    cyc = {c["id"]: c for c in adv["cycles"]}
    text = _farmer_text(parcel, adv)

    t = conf["templates"]
    rr, rt = adv["recommendation"].get("humedad_residual"), adv["recommendation"].get("temporal")
    v = [t["voice_intro"].format(trato=_trato(parcel), municipio=parcel.get("municipality", "su municipio"),
                                 fa10=fecha_larga(doy_of(fr["first_autumn_p10"])))]
    if rr:
        v.append(t["voice_residual_ok"].format(ciclo=cname(rr["cycle_label"]),
                                               fecha=fecha_larga(doy_of(rr["latest"])))
                 if rr["status"] == "ok" else t["voice_residual_none"])
    if rt:
        v.append(t["voice_temporal_ok"].format(ciclo=cname(rt["cycle_label"])) if rt["status"] == "ok"
                 else t["voice_temporal_none"].format(riesgo=max(1, round(rt["p_frost_before_maturity"] * 10))))
    v.append(t["voice_outro"])
    voice = " ".join(" ".join(v).split())

    corto = adv["cycles"][0]
    sms = _farmer_sms(parcel, adv)
    rec_lines = [_rec_line(adv["recommendation"][k], cyc)[2:] for k in ("humedad_residual", "temporal")
                 if k in adv["recommendation"]]
    return {"text": text, "voice": voice, "sms": sms, "short_cycle_latest": corto["latest_p10"],
            "recommendation_lines": rec_lines}


def advise(parcel: dict, clim: dict | None = None) -> dict:
    """Full advice for one parcel: numbers, table, recommendation, Spanish text / voice / SMS, sources."""
    b = _clim_basis(parcel, clim)
    out = {"parcel_id": parcel["parcel_id"], "crop": "maiz_temporal", "elev_m": parcel.get("elev_m"),
           "support": b["support"], "basis": b["basis"], "climatology_source": b["source"],
           "estimates": b["estimates"], **compute(b["used"], parcel.get("elev_m"))}
    conf = cfg()
    out["signed"] = A.advisory_signed()
    out["requires_signature"] = bool(conf.get("requires_signature", True))
    out["signature_note"] = conf.get("signature_note")
    out["frost_note"] = conf.get("frost_note")
    out["risk_note"] = ("Riesgo aproximado: distribución normal ajustada a las fechas p10 y p50 de la primera "
                        "helada de otoño.")
    used = sorted({s for c in conf["cycles"] for s in c.get("src", [])} | {"S2", "S14"}, key=lambda s: int(s[1:]))
    allsrc = sources()
    out["sources"] = {s: allsrc.get(s, "") for s in used}
    out.update(render(parcel, out))
    return out


# ------------------------------------------------------------------ image (WhatsApp-friendly table)
# Fonts for the table image, regular and bold, first one found wins. The system ones differ per machine (none of
# them exists on Windows or in the server image), so the list ends with Bitstream Vera, which ships inside reportlab
# (already a dependency, for the PDF). Pillow's built-in font is not an option: it has no á, é, í, ó, ú, ñ.
_SYSTEM_FONTS = {
    False: ["/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    True: ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}
_NEEDS = "áéíóúñÁÉÍÓÚÑº"       # every font used must draw these


def _bundled_font(bold: bool) -> str | None:
    try:
        import reportlab
        f = Path(reportlab.__file__).parent / "fonts" / ("VeraBd.ttf" if bold else "Vera.ttf")
        return str(f) if f.exists() else None
    except Exception:
        return None


def _draws_spanish(font) -> bool:
    """True when the font has its own glyph for each accented letter (not the «missing» box)."""
    try:
        missing = bytes(font.getmask("\uffff"))
        return all(bytes(font.getmask(ch)) != missing for ch in _NEEDS)
    except Exception:
        return False


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for p in [*_SYSTEM_FONTS[bold], _bundled_font(bold)]:
        if p and Path(p).exists():
            try:
                f = ImageFont.truetype(p, size)
                if _draws_spanish(f):
                    return f
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _font_id() -> str:
    """Name of the font the image is drawn with: part of the cache key, so an image drawn with a font that
    lacked the accents is not served again after the font changes."""
    try:
        return "/".join(_font(20).getname())
    except Exception:
        return "default"


def _risk_color(p: float) -> tuple[int, int, int]:
    return (200, 236, 205) if p < 0.15 else (255, 240, 180) if p < 0.35 else (255, 210, 160) if p < 0.6 else (245, 175, 170)


def render_png(parcel: dict, adv: dict, out_dir: Path) -> Path:
    """Table image: sowing date x cycle -> flowering date and P(frost before maturity). Cached by content hash."""
    from PIL import Image, ImageDraw
    key = hashlib.sha256(repr((parcel["parcel_id"], adv["table"], adv["basis"], adv["signed"], _font_id())).encode()).hexdigest()[:16]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"siembra_{parcel['parcel_id']}_{key}.png"
    if path.exists():
        return path
    cyc = adv["cycles"]
    W, col0, rowh, top = 1000, 190, 74, 150
    colw = (W - col0 - 20) // len(cyc)
    H = top + rowh * (len(adv["table"]) + 1) + 150
    im = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(im)
    def put(xy, text, size, maxw, *, bold=False, fill=(30, 30, 30)):
        """Draw one line, shrinking the font until it fits `maxw`: fonts differ in width from machine to machine."""
        f = _font(size, bold)
        while size > 11 and dr.textlength(text, font=f) > maxw:
            size -= 1
            f = _font(size, bold)
        dr.text(xy, text, font=f, fill=fill)

    full, cellw = W - 40, colw - 22
    put((20, 18), f"Calendario de siembra · parcela {parcel['parcel_id']} · {parcel.get('municipality', '')}",
        30, full, bold=True, fill=(18, 59, 92))
    fr = adv["frost"]
    put((20, 62), f"Primera helada de otoño: 1 de cada 10 años antes del {fr['first_autumn_p10_txt']}; "
                  f"la mitad antes del {fr['first_autumn_p50_txt']}.", 19, full, fill=(40, 40, 40))
    put((20, 90), "Cada casilla: floración aprox. y riesgo de helada ANTES de que el maíz madure.", 19, full,
        fill=(40, 40, 40))
    y = top
    dr.rectangle([20, y, W - 20, y + rowh], fill=(18, 59, 92))
    put((30, y + 22), "Siembra", 21, col0 - 40, bold=True, fill=(255, 255, 255))
    for j, c in enumerate(cyc):
        x = col0 + j * colw
        put((x + 10, y + 10), c["label"].split(" (")[0], 21, cellw, bold=True, fill=(255, 255, 255))
        put((x + 10, y + 40), f"~{c['maturity_days']} días{' *' if c['verify'] else ''}", 16, cellw,
            fill=(220, 230, 240))
    for i, row in enumerate(adv["table"]):
        y = top + rowh * (i + 1)
        dr.rectangle([20, y, W - 20, y + rowh], outline=(210, 210, 210))
        put((30, y + 24), row["sow_txt"], 21, col0 - 40, bold=True)
        for j, ce in enumerate(row["cells"]):
            x = col0 + j * colw
            p = ce["p_frost_before_maturity"]
            dr.rectangle([x + 4, y + 4, x + colw - 4, y + rowh - 4], fill=_risk_color(p))
            put((x + 12, y + 10), f"flor ~{ce['flowering_txt']}", 19, cellw)
            put((x + 12, y + 38), f"helada: {_img_risk(p)}", 21 if p >= 0.35 else 19, cellw, bold=p >= 0.35)
    y = top + rowh * (len(adv["table"]) + 1) + 14
    foot = [f"Base: {adv['basis']}.", adv["risk_note"] + " La fecha depende de cuándo lleguen las lluvias.",
            ("* días a madurez por verificar. " if any(c["verify"] for c in cyc) else "")
            + ("BORRADOR: requiere firma agronómica." if not adv["signed"] else "")]
    fX = _font(16)
    for ln in foot:
        line = ""
        for word in ln.split():          # wrap by measured width, not by character count
            if line and dr.textlength(f"{line} {word}", font=fX) > full:
                dr.text((20, y), line, font=fX, fill=(80, 80, 80))
                y += 22
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            dr.text((20, y), line, font=fX, fill=(80, 80, 80))
            y += 22
    im.save(path, "PNG", optimize=True)
    return path


def _wrap(s: str, n: int) -> list[str]:
    words, out, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return out + ([cur] if cur else [])


# ------------------------------------------------------------------ intent
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:]", " ", s)).strip()


_ASK = re.compile(
    r"\bcuando\s+(?:le\s+|la\s+|lo\s+)?(?:siembro|sembrar|siembre|sembramos|siembran|siembra|sembraria|conviene sembrar|"
    r"debo sembrar|es bueno sembrar|hay que sembrar|me toca sembrar|puedo sembrar)\b"
    r"|\bfechas? (?:de|para|pa) (?:la )?siembra\b|\bfecha (?:para|de|pa) sembrar\b|\bcalendario(?: de siembra)?\b"
    r"|\bque (?:variedad|semilla|ciclo|maiz)\b(?! se)|\bque variedades\b|\bcual (?:variedad|semilla|ciclo)\b"
    r"|\bciclo (?:corto|largo|intermedio|precoz)\b|\bvariedad (?:precoz|temprana)\b")
_BARE = re.compile(r"(?:siembra|sembrar|siembro|variedad|variedades|calendario|semilla)")


def is_planting_question(text: str | None) -> bool:
    """True for «¿cuándo siembro?», «qué variedad», «SIEMBRA», «calendario de siembra»… but not for loss reports
    that merely mention the crop («se quemó la siembra»)."""
    if not text:
        return False
    t = _norm(text)
    return bool(_BARE.fullmatch(t) or _ASK.search(t))
