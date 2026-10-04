import itertools
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pypdf import PdfReader

from backend import pasacme
from backend.packet import build_pdf
from backend.photo import check, make_exif_jpeg, read_exif
from backend.roster import load_roster_file, nearest_station

PARCEL = next(p for p in load_roster_file() if p["parcel_id"] == "P01")
TODAY = date(2026, 10, 4)


def _all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _all_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _all_strings(v)


def report(cause="helada", area=1.0, d="2026-10-03", photos=4, pre=True, crop=None):
    return {"slots": {"crop": crop, "area_ha": area, "cause": cause, "date": d, "notes": "x"},
            "photos": [{"status": "ok"}] * photos, "preregistro": pre}


def test_complete_packet():
    cl = pasacme.build_checklist(PARCEL, report(area=2.0), TODAY)
    assert cl["status"] == "Documentos completos" and cl["complete"]


def test_missing_items_listed():
    cl = pasacme.build_checklist(PARCEL, report(cause=None, area=None, photos=1, pre=None), TODAY)
    assert cl["status"].startswith("Falta: ")
    for m in ("causa del daño", "superficie dañada", "fotos (1 de 4)", "pre-registro PASACME"):
        assert m in cl["status"]


def test_ten_day_notice_clock():
    cl = pasacme.build_checklist(PARCEL, report(d="2026-09-20"), TODAY)
    assert "aviso dentro de 10 días" in cl["status"]
    it = next(i for i in cl["items"] if i["key"] == "aviso_10_dias")
    assert "14 días" in it["detail"]
    ok = pasacme.build_checklist(PARCEL, report(d="2026-10-01"), TODAY)
    assert next(i for i in ok["items"] if i["key"] == "aviso_10_dias")["status"] == "ok"


def test_over_cap_and_under_threshold_are_review_not_decisions():
    big = dict(PARCEL, area_ha=5.0)
    cl = pasacme.build_checklist(big, report(area=4.0), TODAY)
    sup = next(i for i in cl["items"] if i["key"] == "superficie")
    assert sup["status"] == "revisar" and "3 ha" in sup["detail"]
    cl2 = pasacme.build_checklist(PARCEL, report(area=0.5), TODAY)
    assert next(i for i in cl2["items"] if i["key"] == "porcentaje")["status"] == "revisar"


CAUSES = ["helada", "granizo", "sequia", "inundacion", "viento", "otra", None]
AREAS = [None, 0.25, 1.0, 2.5, 3.5, 10.0]
DATES = [None, "2026-10-03", "2026-09-01"]


@pytest.mark.parametrize("cause,area,d,photos,pre",
                         list(itertools.product(CAUSES, AREAS, DATES, [0, 1, 4, 6], [True, False, None])))
def test_checklist_never_outputs_eligibility(cause, area, d, photos, pre):
    cl = pasacme.build_checklist(PARCEL, report(cause, area, d, photos, pre), TODAY, prior_packets_this_year=photos % 2)
    assert cl["status"] == pasacme.COMPLETE or cl["status"].startswith("Falta: ")
    for s in _all_strings(cl):
        low = s.lower()
        assert "elegib" not in low and "eligib" not in low and "procedente" not in low


def test_guard_raises_on_forbidden_words():
    with pytest.raises(ValueError):
        pasacme.assert_no_eligibility({"x": ["El productor es ELEGIBLE"]})
    with pytest.raises(ValueError):
        pasacme.assert_no_eligibility("solicitud aprobada")


def test_photo_exif_checks(tmp_path):
    p = make_exif_jpeg(tmp_path / "a.jpg", when=datetime(2026, 10, 4, 7, 30), lat=PARCEL["lat"] + 0.001,
                       lon=PARCEL["lon"])
    ex = read_exif(p)
    assert ex["taken_at"] == "2026-10-04T07:30:00" and abs(ex["lat"] - (PARCEL["lat"] + 0.001)) < 1e-5
    v = check(p, PARCEL, "2026-10-03", datetime(2026, 10, 4, 8, 0))
    assert v["status"] == "ok" and v["gps"]["distance_m"] < 200
    far = make_exif_jpeg(tmp_path / "b.jpg", when=datetime(2026, 9, 1, 7, 30), lat=19.0, lon=-99.0)
    v2 = check(far, PARCEL, "2026-10-03", datetime(2026, 10, 4, 8, 0))
    assert v2["time"]["status"] == "revisar" and v2["gps"]["status"] == "revisar"


def test_photo_without_exif_uses_receipt_time_and_shared_location(tmp_path):
    from PIL import Image
    p = tmp_path / "noexif.jpg"
    Image.new("RGB", (50, 50)).save(p)
    v = check(p, PARCEL, "2026-10-03", datetime(2026, 10, 4, 8, 0),
              shared_location={"lat": PARCEL["lat"], "lon": PARCEL["lon"]})
    assert v["time"]["source"] == "recepcion" and v["gps"]["source"] == "ubicacion_compartida"
    assert v["status"] == "ok"


def test_packet_pdf_generated(tmp_path):
    photos = []
    for i in range(4):
        p = make_exif_jpeg(tmp_path / f"p{i}.jpg", when=datetime(2026, 10, 4, 7, i), lat=PARCEL["lat"],
                           lon=PARCEL["lon"])
        photos.append({"path": str(p), "verdict": check(p, PARCEL, "2026-10-03", datetime(2026, 10, 4, 8)),
                       "status": "ok"})
    rep = report()
    cl = pasacme.build_checklist(PARCEL, rep, TODAY)
    out = tmp_path / "HEL-TEST.pdf"
    sha = build_pdf(out, packet_id="HEL-TEST", parcel=PARCEL, report=rep, checklist=cl,
                    weather={"parcel_line": "+0.8 °C", "grid_line": "+3.0 °C"},
                    station=nearest_station(PARCEL["lat"], PARCEL["lon"]), photos=photos,
                    transcript="Sí se quemó la milpa, como una hectárea", audit_head="0" * 64,
                    model_source="TEMP_STUB", generated_at=datetime(2026, 10, 4, 8, 30))
    assert out.read_bytes()[:4] == b"%PDF" and len(sha) == 64
    text = "\n".join(pg.extract_text() for pg in PdfReader(str(out)).pages)
    flat = " ".join(text.split())
    for must in ("Documentos completos", "Lo decide la Secretaría del Campo.", "no sustituye la inspección",
                 "Tres Barrancas", "Sí se quemó la milpa", "DATOS FICTICIOS", f"{PARCEL['lat']:.5f}, {PARCEL['lon']:.5f}",
                 "No se tendrá por formulada la solicitud"):
        assert must in flat, must
    assert "elegib" not in flat.lower()
