"""«Registrador en la parcela»: deterministic parsing, WhatsApp confirm-then-store, dashboard upload, model wiring,
audit trail and the WATCH safety net. Offline (archived forecasts come from the demo fixture or the cache)."""
from datetime import date

import pytest

from backend import alerts as A
from backend import parcel_logger as PL
from backend import roster
from backend.conversation import Inbound, handle_inbound

R = {p["parcel_id"]: p for p in roster.load_roster_file()}
TODAY = date(2025, 11, 15)


# ------------------------------------------------------------------ WhatsApp reading parser
@pytest.mark.parametrize("text,tmin,night", [
    ("anoche marcó -2", -2.0, "2025-11-14"),
    ("Anoche marcó −2 grados", -2.0, "2025-11-14"),
    ("el termómetro marcó menos dos", -2.0, "2025-11-14"),
    ("amaneció a 3 bajo cero", -3.0, "2025-11-14"),
    ("la mínima fue de -1,5", -1.5, "2025-11-14"),
    ("antenoche marcó menos uno y medio", -1.5, "2025-11-13"),
    ("hoy en la mañana el termómetro marcaba 0", 0.0, "2025-11-14"),
    ("marcó 4 grados", 4.0, "2025-11-14"),
    ("llegó a -4.5 °C", -4.5, "2025-11-14"),
])
def test_parse_reading(text, tmin, night):
    r = PL.parse_reading(text, TODAY)
    assert r is not None and r["tmin_c"] == tmin and r["night_date"] == night


@pytest.mark.parametrize("text", [
    "se quemó la milpa anoche", "bajó a dos hectáreas", "¿va a helar esta noche?", "hola", "llegó a -40",
    "tengo 3 hectáreas", "marcó el técnico",
])
def test_parse_reading_rejects(text):
    assert PL.parse_reading(text, TODAY) is None


def test_parse_table_formats_header_errors_and_morning_dates():
    txt = "fecha,tmin_c\n2025-11-15,-1,5\n16/11/2025;-2\n2025-11-17\t0.5\n2025-11-18,abc\nxx,1\n2025-11-19,55\n"
    out = PL.parse_table(txt, "morning")
    assert out["rows"] == [("2025-11-14", -1.5), ("2025-11-15", -2.0), ("2025-11-16", 0.5)]
    reasons = [e["reason"] for e in out["errors"]]
    assert len(reasons) == 3 and any("no numérica" in r for r in reasons) and any("fecha" in r for r in reasons)
    assert any("fuera de rango" in r for r in reasons)
    assert PL.parse_table("2025-11-15,1", "evening")["rows"] == [("2025-11-15", 1.0)]


# ------------------------------------------------------------------ WhatsApp flow (stub model)
def say(svc, text, pid="P05", when="2025-11-15T07:30"):
    return handle_inbound(svc, Inbound(R[pid]["phone"], "text", text, received_at=svc.now_local(when)))


def actions(svc, n=30):
    return [e["action"] for e in svc.audit.entries(n)]


def test_whatsapp_reading_confirmed_is_stored_and_audited(svc):
    r = say(svc, "anoche marcó -2")
    q = r.replies[-1]["text"]
    assert "marcó −2.0 °C" in q and "«sí»" in q and r.report_id is None
    assert PL.observations(svc, "P05") == []                       # nothing stored before the «sí»
    r2 = say(svc, "sí")
    assert PL.observations(svc, "P05") == [("2025-11-14", -2.0)]
    assert "Guardado" in r2.replies[-1]["text"] and "1 noches registradas" in r2.replies[-1]["text"]
    acts = actions(svc)
    assert "logger.reading.proposed" in acts and "logger.reading.confirmed" in acts
    assert svc.audit.verify()["ok"]


def test_whatsapp_reading_no_or_other_message_stores_nothing(svc):
    say(svc, "anoche marcó -2")
    r = say(svc, "no")
    assert "No la guardé" in r.replies[-1]["text"] and PL.observations(svc, "P05") == []
    say(svc, "amaneció a 1 bajo cero")
    say(svc, "¿va a helar esta noche?")                            # not a yes/no: the question lapses
    say(svc, "sí")
    assert PL.observations(svc, "P05") == []
    assert "logger.reading.rejected" in actions(svc)


def test_damage_report_is_not_taken_as_a_reading(svc):
    r = say(svc, "anoche bajó a menos 3 y se quemó la milpa, como una hectárea")
    assert r.report_id is not None and PL.observations(svc, "P05") == []
    assert "logger.reading.proposed" not in actions(svc)


# ------------------------------------------------------------------ alert rule with logger support
def fc(support, k, p=0.2, lo=-0.5):
    return {"p_frost": p, "tmin_lo_c": lo, "tmin_c": 2.0, "grid_tmin_c": 3.0, "support": support,
            "drivers": {"logger_nights": k, "logger_expected_mae_c": 1.79}}


def test_watch_safety_net_until_a_full_season():
    assert A.level(fc("logger-anchored (44 noches)", 44)) == "watch"
    assert A.level(fc("logger-anchored (170 noches)", 170)) is None
    assert A.level(fc("logger-anchored (44 noches)", 44, p=0.4)) == "alert"
    s = A.support_lines(fc("logger-anchored (44 noches)", 44))
    assert "registrador en su parcela (44 noches)" in s["soporte_txt"] and "±1.8 °C" in s["soporte_txt"]


# ------------------------------------------------------------------ real model: demo, upload, forecast wiring
def test_demo_view_real_station_never_seen(real_model):
    v = PL.demo_view()
    assert v["site"]["station"] == "15390" and v["until"] == "2025-11-14" and v["k"] == 44
    assert v["before"]["support"] == "terrain-transfer" and v["after"]["support"] == "logger-anchored (44 noches)"
    wb = v["before"]["tmin_hi_c"] - v["before"]["tmin_lo_c"]
    wa = v["after"]["tmin_hi_c"] - v["after"]["tmin_lo_c"]
    assert wa < wb and v["precision"]["mae_c"] < v["precision_before"]["mae_c"]
    assert abs(v["after"]["tmin_c"] - v["observed_tmin_c"]) < abs(v["before"]["tmin_c"] - v["observed_tmin_c"]) + 1.5
    sc = v["season_check"]["cut"]
    assert sc["mae_after_c"] < sc["mae_before_c"]
    assert v["csv_example"].startswith("fecha,tmin_c\n2025-10-02,")


def _seed_archive(svc, pid):
    """Offline test: put archived day-1 forecasts for the parcel's nights in the cache (demo fixture values)."""
    p = R[pid]
    for d, f in PL.demo()["forecasts"].items():
        svc.db.cache_put(PL._arch_key(p, d), f)


def test_dashboard_upload_calibrates_and_changes_the_band(client, real_model):
    svc = client.app.state.svc
    _seed_archive(svc, "P05")
    csv_text = "fecha,tmin_c\n" + "\n".join(f"2025-10-{d:02d},{-1.0 + (d % 3)}" for d in range(2, 32)) + "\nmal,renglón\n"
    r = client.post("/api/logger/P05/readings", data={"date_is": "morning", "scenario": "demo"},
                    files={"file": ("lecturas.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["added"] == 30 and len(j["errors"]) == 1
    assert j["calibration"]["k"] == 30 and j["calibration"]["precision"]["mae_c"] < j["calibration"]["precision_before"]["mae_c"]
    cmp_ = j["compare"]
    assert cmp_["date"] == "2025-11-14" and cmp_["before"]["support"] == "terrain-transfer"
    assert cmp_["after"]["support"] == "logger-anchored (30 noches)" and cmp_["logger_nights_used"] == 30
    assert cmp_["after"]["tmin_c"] != cmp_["before"]["tmin_c"]
    # the main forecast table uses the parcel's logger too
    fc_ = client.get("/api/forecast", params={"scenario": "demo"}).json()
    row = next(x for x in fc_["rows"] if x["parcel"]["parcel_id"] == "P05")
    assert row["forecast"]["support"] == "logger-anchored (30 noches)"
    g = client.get("/api/logger/P05", params={"scenario": "demo"}).json()
    assert g["k"] == 30 and g["n_readings"] == 30 and g["compare"]["after"]["support"].startswith("logger-anchored")
    acts = [e["action"] for e in client.get("/api/audit").json()["entries"]]
    assert "logger.readings.added" in acts and "logger.calibrated" in acts
    assert client.get("/api/audit").json()["verify"]["ok"]


def test_upload_without_archived_forecasts_skips_nights_honestly(client, real_model):
    r = client.post("/api/logger/P10/readings", data={"text": "2025-10-05,1.0\n2025-10-06,-0.5", "date_is": "morning"})
    j = r.json()
    assert j["added"] == 2 and j["calibration"]["k"] == 0
    assert all("sin pronóstico archivado" in s["reason"] for s in j["calibration"]["skipped"])


def test_upload_rejects_empty_and_unparseable(client):
    assert client.post("/api/logger/P05/readings", data={"date_is": "morning"}).status_code == 400
    r = client.post("/api/logger/P05/readings", data={"text": "hola\nadiós,x", "date_is": "morning"})
    assert r.status_code == 422 and r.json()["added"] == 0
    assert client.post("/api/logger/NOPE/readings", data={"text": "2025-10-05,1"}).status_code == 404
