"""Loss-evidence packet PDF (reportlab). Organizes information; never decides."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .slots import CAUSE_LABEL, CROP_LABEL

LOGO = Path(__file__).parent / "data" / "helada-logo.png"  # one colour, 900 x 271 px
STATUS_TXT = {"ok": "OK", "falta": "FALTA", "revisar": "Revisar", "info": "Info"}
STATUS_COL = {"ok": colors.HexColor("#1b7f3b"), "falta": colors.HexColor("#b3261e"),
              "revisar": colors.HexColor("#8a5a00"), "info": colors.HexColor("#44546a")}

_ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_ss["Title"], fontSize=17, leading=21, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=_ss["Heading2"], fontSize=12.5, leading=15, spaceBefore=8, spaceAfter=3,
                    textColor=colors.HexColor("#123b5c"))
P = ParagraphStyle("p", parent=_ss["BodyText"], fontSize=9.5, leading=12.5)
SMALL = ParagraphStyle("s", parent=P, fontSize=8, leading=10, textColor=colors.HexColor("#444444"))
QUOTE = ParagraphStyle("q", parent=P, fontName="Helvetica-Oblique", leftIndent=10,
                       textColor=colors.HexColor("#333333"))
BANNER = ParagraphStyle("b", parent=P, alignment=TA_CENTER, textColor=colors.white, fontName="Helvetica-Bold")
STATUS = ParagraphStyle("st", parent=P, fontSize=13, leading=16, fontName="Helvetica-Bold")


def _p(text: str, style=P) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _kv(rows: list[tuple[str, str]]) -> Table:
    t = Table([[_p(k, SMALL), _p(v)] for k, v in rows], colWidths=[4.2 * cm, 13.3 * cm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3)]))
    return t


def build_pdf(out_path: Path, *, packet_id: str, parcel: dict, report: dict, checklist: dict,
              weather: dict, station: dict, photos: list[dict], transcript: str, audit_head: str,
              model_source: str, generated_at: datetime) -> str:
    """Writes the PDF and returns its sha256."""
    s = report.get("slots") or {}
    story: list = []
    if parcel.get("fictional"):
        b = Table([[Paragraph("DATOS FICTICIOS DE DEMOSTRACIÓN — no es una persona ni una parcela real registrada",
                              BANNER)]], colWidths=[17.5 * cm])
        b.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#6b3fa0"))]))
        story += [b, Spacer(1, 6)]
    if LOGO.exists():
        story.append(RLImage(str(LOGO), width=3.4 * cm, height=3.4 * cm * 271 / 900, hAlign="LEFT"))
    story.append(Paragraph("Paquete de evidencia de siniestro agroclimático", H1))
    story.append(_p(f"Folio Helada {packet_id} · generado {generated_at.strftime('%Y-%m-%d %H:%M')} (hora local) · "
                    f"organiza información para la Delegación; no es una solicitud.", SMALL))

    st_color = "#1b7f3b" if checklist["complete"] else "#b3261e"
    story.append(Spacer(1, 6))
    story.append(Paragraph(f'<font color="{st_color}">{escape(checklist["status"])}</font>', STATUS))
    story.append(_p("Lo decide la Secretaría del Campo.", P))

    story.append(Paragraph("1. Productor y parcela", H2))
    story.append(_kv([
        ("Productor", parcel.get("owner_name", "")),
        ("Municipio / Delegación", f"{parcel.get('municipality', '')} — Delegación {checklist.get('delegacion', '')}"),
        ("Parcela", f"{parcel['parcel_id']} · {parcel.get('area_ha', 0):g} ha · {CROP_LABEL.get(parcel.get('crop'), parcel.get('crop'))} (padrón)"),
        ("Coordenadas (GPS)", f"{parcel['lat']:.5f}, {parcel['lon']:.5f} · altitud {parcel.get('elev_m', '—')} m"),
    ]))

    story.append(Paragraph("2. Evento declarado por el productor", H2))
    area = s.get("area_ha")
    pa = parcel.get("area_ha") or 0
    story.append(_kv([
        ("Causa", CAUSE_LABEL.get(s.get("cause"), "— (falta)")),
        ("Fecha del evento", s.get("date") or "— (falta)"),
        ("Cultivo", CROP_LABEL.get(s.get("crop") or parcel.get("crop"), "—")),
        ("Superficie dañada", f"{area:g} ha ({min(100, 100 * area / pa):.0f}% de la parcela)" if area and pa else "— (falta)"),
        ("Extracción de datos", f"{report.get('slot_engine', 'regex')} · voz: {report.get('asr_engine', '—')}"),
    ]))
    if transcript:
        ex = transcript.strip()
        ex = ex if len(ex) <= 450 else ex[:447] + "…"
        story.append(Spacer(1, 3))
        story.append(_p("Extracto de lo que dijo el productor (transcripción automática):", SMALL))
        story.append(_p(f"“{ex}”", QUOTE))

    story.append(Paragraph("3. Evidencia meteorológica", H2))
    story.append(_kv([
        ("Tmin modelada en la parcela", weather.get("parcel_line", "—")),
        ("Pronóstico regional (celda)", weather.get("grid_line", "—")),
        ("Aviso previo de Helada", weather.get("alert_line", "Sin aviso registrado para esa noche")),
        (f"Estación SMN más cercana", f"{station['name']} ({station['station']}), a {station['distance_km']} km, "
                                     f"{station['alt_m']} m"),
        ("Tmin observada en la estación", weather.get("station_obs", "Pendiente: el SMN publica el dato diario con "
                                                                     "retraso (se agrega al llegar)")),
        ("Fuente del modelo", model_source + ("  — STUB TEMPORAL, sin validación" if model_source == "TEMP_STUB" else "")),
    ]))

    story.append(Paragraph("4. Fotos", H2))
    if photos:
        cells = []
        for ph in photos[:4]:
            img = []
            try:
                img = [RLImage(str(ph["path"]), width=4.1 * cm, height=3.1 * cm, kind="proportional")]
            except Exception:
                pass
            v = ph.get("verdict") or {}
            lines = [v.get("time", {}).get("msg", ""), v.get("gps", {}).get("msg", "")]
            cells.append(img + [_p("\n".join(x for x in lines if x), SMALL)])
        while len(cells) < 4:
            cells.append([""])
        t = Table([cells], colWidths=[4.375 * cm] * 4)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)
        if len(photos) > 4:
            story.append(_p(f"+ {len(photos) - 4} foto(s) más en el expediente digital.", SMALL))
    else:
        story.append(_p("Sin fotos.", P))
    story.append(_p("Nota: WhatsApp borra los datos EXIF de las fotos enviadas como imagen; en ese caso se usa la "
                    "hora de recepción y la ubicación compartida. Estas revisiones no sustituyen la inspección.", SMALL))

    rows = [[_p("Requisito", SMALL), _p("Estado", SMALL), _p("Detalle", SMALL), _p("Origen", SMALL)]]
    for it in checklist["items"]:
        rows.append([_p(it["label"]),
                     Paragraph(f'<font color="{STATUS_COL[it["status"]].hexval().replace("0x", "#")}"><b>'
                               f'{STATUS_TXT[it["status"]]}</b></font>', P),
                     _p(it["detail"], SMALL), _p(it["origin"], SMALL)])
    t = Table(rows, colWidths=[4.8 * cm, 1.7 * cm, 9.0 * cm, 2.0 * cm], repeatRows=1)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef4")),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc"))]))
    story.append(KeepTogether([Paragraph("5. Lista de verificación PASACME (lineamientos 2024)", H2), t]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f'<b>Resultado: {escape(checklist["status"])}</b>', P))

    story.append(Paragraph("6. Documentos que el productor lleva a la ventanilla (Helada no los recibe)", H2))
    for d in checklist["ventanilla_docs"]:
        story.append(_p("[   ]  " + d))
    story.append(Spacer(1, 3))
    story.append(Paragraph(f"<b>{escape(checklist['rule_9_1'])}</b>", P))

    story.append(KeepTogether([Paragraph("7. ¿Qué sigue?", H2), _p(checklist["process"])]))

    story.append(Spacer(1, 10))
    for line in checklist["footer"]:
        story.append(Paragraph(f"<b>{escape(line)}</b>", P))
    story.append(_p(checklist["legend"], SMALL))
    story.append(_p(checklist["version_note"], SMALL))
    story.append(_p(f"Bitácora de auditoría (cadena sha256), cabeza al generar: {audit_head}", SMALL))

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(1.8 * cm, 1.1 * cm, f"Helada · folio {packet_id} · Lo decide la Secretaría del Campo.")
        canvas.drawRightString(letter[0] - 1.8 * cm, 1.1 * cm, f"pág. {doc.page}")
        canvas.restoreState()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=letter, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
                            topMargin=1.6 * cm, bottomMargin=1.8 * cm, title=f"Helada {packet_id}",
                            author="Helada (demo)", subject="Paquete de evidencia de siniestro")
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return hashlib.sha256(out_path.read_bytes()).hexdigest()
