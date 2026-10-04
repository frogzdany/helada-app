import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend import alerts as A

TZ = ZoneInfo("America/Mexico_City")


def fc(p=0.1, lo=1.0, t=2.0, g=3.0, date="2026-10-03"):
    return {"parcel_id": "P01", "date": date, "grid_tmin_c": g, "tmin_c": t, "tmin_lo_c": lo, "tmin_hi_c": t + 2,
            "p_frost": p, "drivers": {"tpi": -27, "elev_diff_m": -296}}


EVENING = datetime(2026, 10, 3, 18, 30, tzinfo=TZ)


def fc_sup(p, lo, anchored):
    return {**fc(p, lo), "support": "station-anchored" if anchored else "terrain-transfer"}


@pytest.mark.parametrize("p,lo,anchored,expected", [
    (0.29, 0.1, True, None), (0.30, 0.1, True, "alert"), (0.30, 0.1, False, "alert"),
    (0.05, 0.0, True, None), (0.05, -0.5, True, None),          # cold band edge alone never alerts
    (0.05, 0.0, False, "watch"), (0.29, -2.0, False, "watch"), (0.05, 0.01, False, None), (0.0, 0.01, True, None),
])
def test_alert_and_watch_levels(p, lo, anchored, expected):
    assert A.level(fc_sup(p, lo, anchored)) == expected
    assert A.triggers(fc_sup(p, lo, anchored)) is (expected == "alert")


def test_watch_ignores_weekly_cap_and_has_no_voice():
    h = [EVENING - timedelta(days=2), EVENING - timedelta(days=1)]
    d = A.decide(fc_sup(0.1, -1.0, False), h, EVENING)
    assert (d.action, d.reason, d.level) == ("send", "vigilancia", "watch")
    parcel = {"parcel_id": "P10", "owner_name": "Petra Segundo (ficticia)", "municipality": "San Felipe del Progreso",
              "crop": "maiz_temporal"}
    m = A.render_watch(parcel, fc_sup(0.1, -1.0, False), EVENING)
    assert m["voice"] is None and "no es aviso" in m["text"] and "registrador" in m["text"]
    assert len(m["sms"]) <= 160 and m["sms"].isascii() and "BAJA=salir" in m["sms"]


def test_no_risk_is_skipped():
    d = A.decide(fc(0.1, 1.0), [], EVENING)
    assert (d.action, d.reason) == ("skip", "sin_riesgo")


def test_send_in_window_queue_outside():
    assert A.decide(fc(0.4), [], EVENING).action == "send"
    noon = EVENING.replace(hour=12)
    assert A.decide(fc(0.4), [], noon).as_dict() == {"action": "queue", "reason": "fuera_de_ventana", "triggered": True,
                                                     "level": "alert"}
    assert A.decide(fc(0.4), [], noon, override_window=True).action == "send"
    assert A.decide(fc(0.4), [], EVENING.replace(hour=20, minute=0)).action == "queue"   # window is [18:00, 20:00)


def test_weekly_cap_rolling_7_days():
    h = [EVENING - timedelta(days=2), EVENING - timedelta(days=1)]
    assert A.decide(fc(0.9), h, EVENING).reason == "tope_semanal"
    # the oldest alert drops out of the rolling window after 7 days
    later = EVENING + timedelta(days=5, minutes=1)
    assert A.decide(fc(0.9), h, later).action == "send"
    assert A.decide(fc(0.9), h[:1], EVENING).action == "send"


def test_duplicate_and_optout():
    assert A.decide(fc(0.9), [], EVENING, already_for_date=True).reason == "ya_avisado"
    assert A.decide(fc(0.9), [], EVENING, opted_out=True).reason == "baja"


def test_render_spanish_and_sms():
    parcel = {"parcel_id": "P01", "owner_name": "Aurelio Mendoza (ficticio)", "trato": "don",
              "municipality": "Almoloya de Juárez", "crop": "maiz_temporal"}
    m = A.render(parcel, fc(0.31, -0.4, 0.8, 3.0), EVENING)
    assert "don Aurelio" in m["voice"] and "3 de cada 10" in m["voice"]
    assert "Responda uno para más" in m["voice"] and "Si el grano ya está duro" in m["voice"]
    assert "sábado 3 al domingo 4 de octubre" in m["detail"]
    lines = m["text"].split("\n")            # short alert: <= 4 lines, 1 action, "1 para más"
    assert len(lines) <= 4 and "31%" in lines[1] and "Responda 1 para más" in lines[-1] and "BAJA" in m["text"]
    assert "Noche del" not in m["text"] and "Base:" not in m["text"]
    assert "BAJA" in m["detail"]
    assert len(m["sms"]) <= 160 and m["sms"].isascii()


def _cold_clear_grid(monkeypatch):
    """Live path with a fixed cold, clear, calm grid forecast for any night (the stub answers in tests)."""
    from backend import model_adapter

    def fake(parcels, night_date, **kw):
        return {p["parcel_id"]: {"grid_tmin_c": 1.0, "cloud_cover": 5.0, "wind_ms": 1.0, "rh": 60.0,
                                 "grid_elev_m": p.get("elev_m"), "source": "test"} for p in parcels}
    monkeypatch.setattr(model_adapter, "fetch_grid_forecast", fake)


def test_service_cap_and_dedupe(svc, monkeypatch, real_model):
    _cold_clear_grid(monkeypatch)
    r1 = svc.send_alerts("2026-10-03", now="2026-10-03T18:30", with_audio=False)
    assert r1["summary"].get("sent", 0) >= 3
    r_dup = svc.send_alerts("2026-10-03", now="2026-10-03T18:40", with_audio=False)
    assert "sent" not in r_dup["summary"] and r_dup["summary"].get("ya_avisado", 0) == r1["summary"]["sent"]
    r2 = svc.send_alerts("2026-10-04", now="2026-10-04T18:30", with_audio=False)
    assert r2["summary"]["sent"] == r1["summary"]["sent"]
    r3 = svc.send_alerts("2026-10-05", now="2026-10-05T18:30", with_audio=False)
    assert "sent" not in r3["summary"] and r3["summary"]["tope_semanal"] == r1["summary"]["sent"]
    # 7 days after the first one, P01 can be alerted again
    r4 = svc.send_alerts("2026-10-10", now="2026-10-10T18:31", with_audio=False, parcel_ids=["P01"])
    assert r4["summary"] == {"sent": 1}


def test_no_model_sends_no_numbers_only_not_sure(svc, monkeypatch):
    """With the backup calculation (TEMP_STUB) no alert goes out. The farmer gets «no estoy seguro,
    pregunte a su técnico» with no temperature or probability, once per night, and it is in the audit log."""
    _cold_clear_grid(monkeypatch)
    r = svc.send_alerts("2026-01-10", now="2026-01-10T18:30", with_audio=False)
    assert r["model_source"] == "TEMP_STUB" and r["summary"] == {"no_seguro": 12}
    rows = svc.db.all("SELECT level, status, reason, text, sms_text, forecast FROM alerts")
    assert len(rows) == 12 and {(x["level"], x["status"], x["reason"]) for x in rows} == {("unsure", "sent", "sin_modelo")}
    for x in rows:
        for body in (x["text"], x["sms_text"]):
            assert "No estoy seguro" in body and "tecnico" in A.ascii_sms(body).lower()
            assert "°C" not in body and "%" not in body and "de cada 10" not in body
            # the parcel id and the technician's phone are the only numbers
            assert not re.search(r"\d", re.sub(r"\bP\d\d\b|722 000 0100|7220000100", "", body))
        assert len(x["sms_text"]) <= 160 and x["sms_text"].isascii()
        assert "tmin_c" not in x["forecast"] and "p_frost" not in x["forecast"]
    skipped = [e for e in svc.audit.entries(60) if e["action"] == "alert.skipped"]
    assert len(skipped) == 1 and skipped[0]["payload"]["reason"] == "sin_modelo"
    # same night again: nothing new
    again = svc.send_alerts("2026-01-10", now="2026-01-10T18:40", with_audio=False)
    assert again["summary"] == {"ya_avisado": 12}
    # outside the evening window nothing is sent and nothing is queued
    noon = svc.send_alerts("2026-01-11", now="2026-01-11T12:00", with_audio=False)
    assert noon["summary"] == {"sin_modelo": 12} and not svc.db.one("SELECT id FROM alerts WHERE status='queued'")
    # own weekly cap: two «no estoy seguro» notices per parcel per 7 days
    assert svc.send_alerts("2026-01-11", now="2026-01-11T18:30", with_audio=False)["summary"] == {"no_seguro": 12}
    assert svc.send_alerts("2026-01-12", now="2026-01-12T18:30", with_audio=False)["summary"] == {"tope_semanal": 12}


def test_not_sure_notice_does_not_block_or_feed_a_real_alert(svc, monkeypatch):
    """The model comes back the same evening: the real alert still goes out, and the «no estoy seguro» notice is
    not read as tonight's alert by the forecast reply."""
    from backend import model_adapter
    from backend.conversation import Inbound, handle_inbound
    _cold_clear_grid(monkeypatch)
    svc.send_alerts("2026-01-10", now="2026-01-10T18:30", with_audio=False, parcel_ids=["P01"])
    phone = svc.db.one("SELECT phone FROM parcels WHERE parcel_id='P01'")["phone"]
    res = handle_inbound(svc, Inbound(phone, "text", "¿va a helar hoy?", received_at=svc.now_local("2026-01-10T18:45")))
    txt = res.replies[0]["text"]
    assert "No estoy seguro" in txt and "técnico" in txt and "°C" not in txt and "de cada 10" not in txt
    import helada_model
    monkeypatch.setattr(model_adapter, "_hm", helada_model)
    monkeypatch.setattr(model_adapter, "LAST_ERROR", None)
    r = svc.send_alerts("2026-01-10", now="2026-01-10T19:00", with_audio=False, parcel_ids=["P01"])
    assert r["model_source"] == "helada_model" and r["results"][0]["reason"] != "ya_avisado"


def test_demo_is_fixed_replay_night(svc):
    # the demo replays a real night; whatever date is asked, the forecast values belong to 2025-11-14
    fc = svc.forecast("2026-10-03", "demo")
    assert fc["date"] == "2025-11-14" and fc["scenario"] == "demo"
    p01 = next(r for r in fc["rows"] if r["parcel"]["parcel_id"] == "P01")
    assert p01["grid"]["source"].startswith("replay: Open-Meteo ecmwf_ifs025")
    assert p01["truth"]["observed_tmin_c"] == -3.0 and p01["truth"]["station"]["id"] == "15282"
    p05 = next(r for r in fc["rows"] if r["parcel"]["parcel_id"] == "P05")
    assert p05["truth"]["observed_tmin_c"] is None       # terrain-transfer parcel: no station, no truth


def test_queue_then_flush_in_window(svc, real_model):
    r = svc.send_alerts(None, scenario="demo", now="2025-11-14T12:00", with_audio=False)
    # replay night, held-out model: 6 alerts (P >= 30%) + 1 watch (P10, terrain-transfer) queued
    assert r["summary"]["fuera_de_ventana"] == 6 and r["summary"]["vigilancia_fuera_de_ventana"] == 1
    queued = 7
    assert svc.flush_queue("2025-11-14T15:00") == []                   # still outside window
    sent = svc.flush_queue("2025-11-14T18:05")
    assert len(sent) == queued and all(s["status"] == "sent" for s in sent)
    msgs = svc.messages("+527220000001")
    assert any("Aviso de Helada" in (m["text"] or "") for m in msgs)


def test_form_of_address_comes_from_the_roster_never_from_the_name():
    """A real roster has no «(ficticia)» tag. Without the `trato` field the greeting is the first name."""
    teresa = {"parcel_id": "P02", "owner_name": "Teresa Gómez", "municipality": "Almoloya de Juárez"}
    assert A.trato(teresa) == "Teresa" and A.trato({**teresa, "trato": "doña"}) == "doña Teresa"
    assert A.trato({"owner_name": ""}) == "productor"
    voice = A.render({**teresa, "crop": "maiz_temporal"}, fc(0.6, -2.0, -1.0, 2.0), EVENING)["voice"]
    assert voice.startswith("Buenas tardes, Teresa.") and "don " not in voice
    from backend import roster
    assert all(p.get("trato") in ("don", "doña") for p in roster.load_roster_file())


def test_probability_wording_never_says_one_in_ten_for_a_warm_night():
    """At +10 °C the chance is «menos de 1 de cada 10», not «1 de cada 10»."""
    assert A.prob_voz(0.0) == "menos de 1 de cada 10" and A.prob_voz(0.04) == "menos de 1 de cada 10"
    assert A.prob_voz(0.05 + 1e-9) == "1 de cada 10" and A.prob_voz(0.31) == "3 de cada 10" and A.prob_voz(1) == "10 de cada 10"
    assert A.prob_sms(0.0) == "<1/10" and A.prob_sms(0.6) == "6/10"


def test_not_sure_gives_the_technicians_phone():
    """«pregunte a su técnico» comes with a phone number, the same one the phone page shows, and says it
    is fictional in the demo."""
    import json
    from pathlib import Path
    c = A.advisory()["contact"]
    m = A.render_unsure({"parcel_id": "P01", "municipality": "Almoloya de Juárez"})
    assert c["phone"] in m["text"] and "ficticio" in m["text"]
    assert "7 2 2, 0 0 0, 0 1 0 0" in m["voice"] and "demostración" in m["voice"]
    assert "Tecnico: 7220000100" in m["sms"] and len(m["sms"]) <= 160 and m["sms"].isascii()
    pack = json.loads((Path(__file__).resolve().parents[1] / "static/movil/data/pack.json").read_text(encoding="utf-8"))
    assert pack["contact"] == c


def test_offline_without_a_saved_forecast_is_not_sure(svc, real_model):
    """Offline with nothing saved, the only weather input is a monthly average. The model runs on it, but
    its numbers are not a forecast: no alert goes out, the farmer gets «no estoy seguro» and the phone to call."""
    from backend import conversation
    from backend.conversation import Inbound, handle_inbound
    fc = svc.forecast("2026-01-10")
    assert fc["model_source"] == "helada_model" and fc["unsure_reason"] == "sin_pronostico"
    assert any(r["level"] == "alert" for r in fc["rows"])       # the monthly average alone would have alerted
    r = svc.send_alerts("2026-01-10", now="2026-01-10T18:30", with_audio=False)
    assert r["unsure_reason"] == "sin_pronostico" and r["summary"] == {"no_seguro": 12}
    rows = svc.db.all("SELECT level, reason, text, forecast FROM alerts")
    assert {(x["level"], x["reason"]) for x in rows} == {("unsure", "sin_pronostico")}
    assert all("°C" not in x["text"] and "722 000 0100" in x["text"] and "p_frost" not in x["forecast"] for x in rows)
    skipped = [e for e in svc.audit.entries(60) if e["action"] == "alert.skipped"]
    assert len(skipped) == 1 and skipped[0]["payload"]["reason"] == "sin_pronostico"
    from backend import roster
    p = roster.get_parcel(svc.db, "P01")
    res = handle_inbound(svc, Inbound(p["phone"], "text", "¿va a helar hoy?", received_at=svc.now_local("2026-01-11T12:00")))
    txt = res.replies[0]["text"]
    assert "No estoy seguro" in txt and "722 000 0100" in txt and "°C" not in txt and "de cada 10" not in txt
    # the loss packet shows no model number for a night without a forecast
    assert conversation._weather(svc, p, "2026-01-10", "helada")["summary"].startswith("Sin dato del modelo")


def test_offline_repeats_the_saved_forecast_and_says_so(svc, real_model, monkeypatch):
    """A forecast computed with a connection is kept per parcel and night. Offline, the server answers
    with it, marked as saved; a night nobody computed, or one computed too far ahead, stays «no estoy seguro»."""
    from backend import model_adapter

    class Online:       # stands for helada_model downloading the regional forecast itself
        def __getattr__(self, k):
            return getattr(real_model, k)

        def predict(self, parcels, night, forecast=None, **kw):
            if forecast is None:
                forecast = {p.parcel_id: {"grid_tmin_c": 1.0, "cloud_cover": 5.0, "wind_ms": 1.0, "rh": 60.0}
                            for p in parcels}
            return real_model.predict(parcels, night, forecast, **kw)

    monkeypatch.setattr(model_adapter, "_hm", Online())
    object.__setattr__(svc.s, "offline", False)
    online = svc.forecast("2026-10-04")
    far_ahead = svc.forecast("2026-12-24")
    assert online["unsure_reason"] is None and far_ahead["unsure_reason"] is None
    object.__setattr__(svc.s, "offline", True)
    saved = svc.forecast("2026-10-04")
    assert saved["unsure_reason"] is None and all("pronóstico guardado" in s for s in saved["grid_sources"])
    assert [r["forecast"] for r in saved["rows"]] == [r["forecast"] for r in online["rows"]]
    one = svc.forecast("2026-10-04", parcel_ids=["P07"])           # the bot asks for one parcel: same saved answer
    assert one["unsure_reason"] is None and one["rows"][0]["forecast"] == online["rows"][6]["forecast"]
    r = svc.send_alerts("2026-10-04", now="2026-10-04T18:30", with_audio=False)
    assert r["summary"].get("sent", 0) >= 3 and "no_seguro" not in r["summary"]
    created = [e for e in svc.audit.entries(80) if e["action"] == "alert.created"]
    assert created and all("pronóstico guardado" in e["payload"]["grid_source"] for e in created)
    assert svc.forecast("2026-10-05")["unsure_reason"] == "sin_pronostico"      # never computed
    assert svc.forecast("2026-12-24")["unsure_reason"] == "sin_pronostico"      # computed, but too far ahead


def test_alert_list_carries_the_sms_version(svc, real_model, monkeypatch):
    """The dashboard shows what a basic phone receives: plain ASCII, 160 characters or fewer."""
    _cold_clear_grid(monkeypatch)
    svc.send_alerts("2026-10-03", now="2026-10-03T18:30", with_audio=False)
    sent = [a for a in svc.list_alerts() if a["status"] == "sent"]
    assert sent and all(a["sms_text"].isascii() and 0 < len(a["sms_text"]) <= 160 for a in sent)


def test_replay_night_voice_notes_are_recorded(svc, real_model, monkeypatch):
    """The demo's replay night plays voice notes recorded ahead (backend/data/voice). The recording is found by the
    exact text, so a changed alert wording, roster or advisory table needs `scripts/make_demo_voice.py` run again:
    until then piper would speak that alert, and this test says so."""
    from backend import service, tts
    spoken = []
    monkeypatch.setattr(service.tts, "synthesize", lambda text, *a, **kw: spoken.append(text))
    r = svc.send_alerts("2025-11-14", scenario="demo", now="2025-11-14T18:30", override_window=True)
    assert r["summary"]["sent"] == len(spoken) == 6
    missing = [t for t in spoken if tts.prerecorded(t) is None]
    assert not missing, f"no recording for: {missing}"


def test_recorded_voice_note_is_played_as_recorded(tmp_path):
    """A text with a recording gets that file, with or without a speech engine on the machine; «off» stays off."""
    import json
    from backend import tts
    files = json.loads((tts.VOICE_DIR / "manifest.json").read_text(encoding="utf-8"))["files"]
    name, info = next((n, i) for n, i in files.items() if n != tts.SAMPLE_VOICE.name)
    out = tts.synthesize(info["text"], tmp_path, pref="auto")
    assert out is not None and out.read_bytes() == (tts.VOICE_DIR / name).read_bytes()
    assert tts.synthesize(info["text"], tmp_path / "off", pref="off") is None
    assert tts.SAMPLE_VOICE.exists()

