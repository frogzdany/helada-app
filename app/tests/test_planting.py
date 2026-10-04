"""«¿Cuándo siembro?» planting advisor, its WhatsApp routing, and the SMN observed Tmin in packets."""
import io
import json

import pytest
from pypdf import PdfReader

from backend import conversation, planting, roster, smn_obs
from backend.conversation import Inbound, handle_inbound

R = {p["parcel_id"]: p for p in roster.load_roster_file()}


def phone(pid):
    return R[pid]["phone"]


# ------------------------------------------------------------------ pure computation
CLIM = {"last_spring_frost_doy_p50": 98, "last_spring_frost_doy_p90": 131,
        "first_autumn_frost_doy_p10": 276, "first_autumn_frost_doy_p50": 306}


def test_cycle_days_scale_with_altitude():
    conf = planting.cfg()
    corto = conf["cycles"][0]
    assert planting.cycle_days(corto, 2250, 4.0) == (70, 136)            # reference altitude: table values
    fd, md = planting.cycle_days(corto, 2682, 4.0)                      # P01, 432 m higher: +17%
    assert (fd, md) == (82, 160)
    assert planting.cycle_days(corto, None, 4.0) == (70, 136)


def test_compute_windows_and_rule():
    a = planting.compute(CLIM, 2250)
    corto, inter, largo = a["cycles"]
    # rule: sowing + maturity days (+ margin 0) <= first autumn frost p10 (risk-averse) / p50
    assert corto["latest_p10_doy"] == 276 - 136 and corto["latest_p10"] == "05-20"
    assert corto["latest_p50_doy"] == 306 - 136
    assert inter["latest_p10_doy"] == 276 - 155
    assert largo["latest_p10_doy"] < inter["latest_p10_doy"] < corto["latest_p10_doy"]
    assert a["frost"]["first_autumn_p10_txt"] == "3 oct" and a["frost"]["frost_free_days"] == 208
    # risk grows with later sowing and with longer cycles
    for row in a["table"]:
        ps = [c["p_frost_before_maturity"] for c in row["cells"]]
        assert ps == sorted(ps)
    col = [row["cells"][0]["p_frost_before_maturity"] for row in a["table"]]
    assert col == sorted(col)
    # at the p10 latest date, the risk before maturity is ~10% (normal fitted to p10/p50)
    assert planting._phi((corto["latest_p10_doy"] + 136 - 306) / a["frost"]["sigma_days"]) == pytest.approx(0.10, abs=0.005)
    assert a["recommendation"]["humedad_residual"]["cycle"] == "largo"      # criollo reference is 2 640 m: shorter here
    rec = planting.compute(CLIM, 2600)["recommendation"]
    assert rec["humedad_residual"]["status"] == "ok" and rec["humedad_residual"]["cycle"] == "corto"
    assert rec["temporal"]["status"] == "none" and 0.3 < rec["temporal"]["p_frost_before_maturity"] < 0.9


def test_late_frost_parcel_fits_long_cycle_even_with_rains():
    warm = {**CLIM, "first_autumn_frost_doy_p10": 340, "first_autumn_frost_doy_p50": 350}
    rec = planting.compute(warm, 2250)["recommendation"]
    assert rec["temporal"]["status"] == "ok" and rec["temporal"]["cycle"] == "largo"


# ------------------------------------------------------------------ real helada_model, 3 parcels
def test_planting_window_three_parcels_real_model(real_model):
    out = {pid: planting.advise(R[pid]) for pid in ("P01", "P03", "P10")}
    p01, p03, p10 = out["P01"], out["P03"], out["P10"]
    assert p01["climatology_source"] == "helada_model"
    assert p01["support"] == p03["support"] == "station-anchored"
    assert "15282" in p01["basis"] and "15086" in p03["basis"]
    assert len(p01["estimates"]) == 1
    # P01, Tres Barrancas (2 682 m): first autumn frost p10 3 Oct, p50 2 Nov (station record 1991-2025)
    assert p01["frost"]["first_autumn_p10"] == "10-03" and p01["frost"]["first_autumn_p50"] == "11-02"
    corto = p01["cycles"][0]
    assert corto["maturity_days"] == 160 and corto["latest_p10"] == "04-26" and corto["latest_p50"] == "05-26"
    assert p01["recommendation"]["humedad_residual"]["cycle"] == "corto"
    assert p01["recommendation"]["temporal"]["status"] == "none"
    # P10: terrain-transfer -> conservative of (terrain estimate, nearest SMN station 15076)
    assert p10["support"] == "terrain-transfer" and "sin estación" in p10["basis"] and "15076" in p10["basis"]
    assert len(p10["estimates"]) == 2
    fa10 = min(e["first_autumn_frost_doy_p10"] for e in p10["estimates"])
    assert p10["frost"]["first_autumn_p10"] == planting.mmdd(fa10)
    # every parcel: longer cycle -> earlier latest sowing; draft flag + sources
    for a in out.values():
        lat = [c["latest_p10_doy"] for c in a["cycles"]]
        assert lat == sorted(lat, reverse=True)
        assert a["requires_signature"] and not a["signed"] and "todavía no lo revisó un agrónomo" in a["text"]
        assert {"S4", "S9", "S11", "S14"} <= set(a["sources"]) and all(a["sources"].values())
        assert "lluvias" in a["text"] and len(a["sms"]) <= 160 and a["sms"].isascii()


def test_planting_png(real_model, tmp_path):
    a = planting.advise(R["P01"])
    p = planting.render_png(R["P01"], a, tmp_path)
    from PIL import Image
    im = Image.open(p)
    assert im.format == "PNG" and im.size[0] == 1000
    assert planting.render_png(R["P01"], a, tmp_path) == p          # cached by content


def test_api_planting(client, real_model):
    r = client.get("/api/planting/P01")
    assert r.status_code == 200
    a = r.json()
    assert a["parcel_id"] == "P01" and len(a["table"]) == 7 and len(a["cycles"]) == 3
    assert a["recommendation_lines"] and not a["signed"]
    assert client.get("/api/planting/NOPE").status_code == 404


# ------------------------------------------------------------------ intent routing
@pytest.mark.parametrize("text", [
    "¿Cuándo siembro?", "cuando siembro este año", "SIEMBRA", "Siembra.", "¿Qué variedad me conviene?",
    "que semilla siembro", "calendario de siembra", "fecha de siembra", "cuándo es bueno sembrar",
    "cual ciclo me conviene", "tiene variedad precoz?", "¿Cuándo le siembro al maíz?", "variedades",
])
def test_is_planting_question(text):
    assert planting.is_planting_question(text)


@pytest.mark.parametrize("text", [
    "Se quemó la siembra anoche", "se me heló toda la siembra", "Sí, se quemó la milpa, como una hectárea",
    "fue helada", "no", "listo", "BAJA", "hola", "", None, "la siembra de papa se perdió con el granizo",
])
def test_is_not_planting_question(text):
    assert not planting.is_planting_question(text)


def test_flow_planting_text_voice_image(svc):
    res = handle_inbound(svc, Inbound(phone("P01"), "text", "¿Cuándo siembro?"))
    assert res.report_id is None and res.packet is None and res.planting
    rep = res.replies[0]
    assert "Siembra de maíz · parcela P01" in rep["text"]
    assert "a más tardar" in rep["text"] or "siembre entre el" in rep["text"]
    assert rep["media_url"] and rep["media_url"].endswith(".png")
    msgs = svc.messages(phone("P01"))
    assert [m["kind"] for m in msgs if m["direction"] == "out"] == ["text", "image"]   # TTS off in tests
    assert svc.db.one("SELECT COUNT(*) n FROM reports")["n"] == 0
    assert any(e["action"] == "planting.advice" for e in svc.audit.entries(50))


def test_flow_planting_by_voice_note(svc, tmp_path, monkeypatch):
    from backend import asr as asr_mod
    monkeypatch.setattr(asr_mod, "transcribe", lambda path, **k: asr_mod.Transcript("¿Qué variedad me conviene sembrar?", "mock"))
    audio = tmp_path / "n.ogg"
    audio.write_bytes(b"OggS")
    res = handle_inbound(svc, Inbound(phone("P03"), "audio", None, audio))
    assert res.planting and "parcela P03" in res.replies[0]["text"]


def test_flow_planting_voice_note_sent_when_tts_available(svc, monkeypatch, tmp_path):
    fake = svc.s.media_dir / "tts" / "x.ogg"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"OggS")
    seen = {}

    def synth(text, out_dir, **k):
        seen["text"] = text
        return fake
    monkeypatch.setattr(conversation.tts, "synthesize", synth)
    res = handle_inbound(svc, Inbound(phone("P01"), "text", "siembra"))
    assert res.replies[0]["audio_url"].endswith("x.ogg")
    assert seen["text"].startswith("Don Aurelio") and "lluvias" in seen["text"]
    kinds = [m["kind"] for m in svc.messages(phone("P01")) if m["direction"] == "out"]
    assert kinds == ["text", "audio", "image"]


def test_planting_question_does_not_break_open_loss_report(svc):
    now = svc.now_local("2025-11-15T08:00")
    r1 = handle_inbound(svc, Inbound(phone("P01"), "text", "se heló la milpa anoche", received_at=now))
    assert r1.report_id and "cuánto terreno" in r1.replies[0]["text"]
    r2 = handle_inbound(svc, Inbound(phone("P01"), "text", "¿y cuándo siembro el otro año?", received_at=now))
    assert r2.planting and r2.report_id is None
    r3 = handle_inbound(svc, Inbound(phone("P01"), "text", "como una hectárea", received_at=now))
    assert r3.report_id == r1.report_id and r3.replies[0]["text"].startswith("Entendí: ") and "fotos" in r3.replies[1]["text"]


def test_loss_report_mentioning_siembra_is_not_planting(svc):
    res = handle_inbound(svc, Inbound(phone("P01"), "text", "Se quemó la siembra anoche con la helada",
                                      received_at=svc.now_local("2025-11-15T08:00")))
    assert res.planting is None and res.report_id


def test_help_mentions_siembra(svc):
    res = handle_inbound(svc, Inbound(phone("P01"), "text", "hola"))
    assert "SIEMBRA" in res.replies[0]["text"]


# ------------------------------------------------------------------ SMN observed Tmin (packet)
def test_smn_observed_real_value():
    o = smn_obs.observed_for_night("15282", "2025-11-14")        # demo night; SMN 08:00 reading dated 11-15
    assert o["status"] == "ok" and o["tmin_c"] == -3.0 and o["obs_date"] == "2025-11-15"
    assert o["prev_tmin_c"] == -0.5


def test_smn_observed_not_published_yet_says_lag():
    d = smn_obs.data()
    last = d["stations"]["15026"]["last_date"]                    # Enyeje (P12): file stops early
    o = smn_obs.observed_for_night("15026", "2026-01-10")
    assert o["status"] == "not_published" and o["last_date"] == last and o["lag_days"] > 60
    assert o["text"].startswith("Observación SMN aún no publicada (rezago típico de esta estación:")
    assert "meses" in o["text"] and d["retrieved"] in o["text"]
    f = smn_obs.observed_for_night("15282", "2026-10-10")         # after the file was retrieved
    assert f["status"] == "not_published" and "días" in f["text"]


def test_smn_observed_missing_and_no_file(monkeypatch, tmp_path):
    d = smn_obs.data()
    gap = next(((sid, st["start"], i) for sid, st in d["stations"].items() if st["start"]
                for i, v in enumerate(st["tmin"]) if v is None and i > 0), None)
    if gap:
        from datetime import date, timedelta
        sid, start, i = gap
        night = (date.fromisoformat(start) + timedelta(days=i - 1)).isoformat()
        o = smn_obs.observed_for_night(sid, night)
        assert o["status"] == "missing" and "dato faltante" in o["text"]
    monkeypatch.setenv("HELADA_SMN_OBS", str(tmp_path / "none.json.gz"))
    o = smn_obs.observed_for_night("15282", "2025-11-14")
    assert o["status"] == "no_data" and o["tmin_c"] is None


def test_weather_block_uses_observed_value_on_live_night(svc):
    w = conversation._weather(svc, R["P01"], "2025-11-14", "helada")
    assert w["station_obs_status"] == "ok"
    assert w["station_obs"].startswith("-3.0 °C — SMN 15282") and "dato real del archivo diario SMN" in w["station_obs"]
    assert "Lectura del 2025-11-14 (noche anterior): -0.5 °C" in w["station_obs"]
    w10 = conversation._weather(svc, R["P10"], "2025-11-14", "helada")   # terrain-transfer: station 7.9 km away
    assert "la parcela puede diferir" in w10["station_obs"]


def test_packet_says_observation_not_published(client):
    ph, now = phone("P12"), "2026-01-11T08:00"
    post = lambda t: client.post("/api/sim/inbound", data={"phone": ph, "text": t, "now": now}).json()
    post("se heló el haba anoche, una hectárea")
    post("listo")
    out = post("no")
    pk = out["packet"]
    assert pk
    pdf = client.get(pk["url"]).content
    text = " ".join(" ".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf)).pages).split())
    assert "Observación SMN aún no publicada (rezago típico de esta estación" in text
    assert "Pendiente: el SMN publica" not in text


def test_live_forecast_rows_carry_observation(svc):
    fc = svc.forecast("2025-11-14")
    rows = {r["parcel"]["parcel_id"]: r for r in fc["rows"]}
    assert rows["P01"]["observed"]["status"] == "ok" and rows["P01"]["observed"]["tmin_c"] == -3.0
    assert rows["P10"]["observed"] is None
    later = {r["parcel"]["parcel_id"]: r for r in svc.forecast("2026-01-10")["rows"]}
    assert later["P12"]["observed"]["status"] == "not_published"


def test_farmer_answer_is_short_plain_and_carries_the_warnings(real_model):
    """The chat answer is one recommendation per way of sowing, in plain words, with the spring-frost and
    the June warnings; the SMS fits one segment, keeps the «not reviewed» mark and never says «anos»."""
    for pid in R:
        a = planting.advise(R[pid])
        txt, sms = a["text"], a["sms"]
        assert len(txt) <= 800, (pid, len(txt))
        for jargon in ("n.º", "humedad residual", "punta de riego", "riesgo 1 de 2", "*", "Base:", "Requiere firma"):
            assert jargon not in txt, (pid, jargon)
        assert "Pregunte a su técnico" in txt
        # the spring-frost warning is there exactly when a mid-April sowing would be up before the last frost
        early = planting.doy_of(a["emergence_after_last_frost_from"]) > planting.doy_of("04-15")
        assert early == ("última helada" in txt or "todavía puede helar hasta el" in txt), pid
        assert early == ("Puede helar hasta" in sms), pid
        assert "Esto todavía no lo revisó un agrónomo." in txt
        assert len(sms) <= 160 and sms.isascii() and sms.startswith(f"SIEMBRA {pid} (sin revisar): "), (pid, sms)
        assert "anos" not in sms and "Borrador" not in sms and "tecnico" in sms
    p01 = planting.advise(R["P01"])
    assert "siembre a más tardar el 26 de abril" in p01["text"] and "hasta el 11 de mayo" in p01["text"]
    assert "unos 6 de cada 10 años la helada llega antes de que el maíz madure" in p01["text"]
    assert p01["sms"] == ("SIEMBRA P01 (sin revisar): maiz ciclo corto, siembre hasta el 26 abr. Puede helar hasta 11 may. "
                          "Si siembra en junio, 6 de 10 veces se hiela. Vea a su tecnico.")


def test_table_image_font_draws_accents_on_any_machine(monkeypatch, tmp_path):
    """On Windows and in the server image none of the system fonts exists. The image must still draw
    «Juárez», «días», «otoño»: the font bundled with reportlab is used, never Pillow's built-in one."""
    from backend import planting
    monkeypatch.setattr(planting, "_SYSTEM_FONTS", {False: ["/no/such/font.ttf"], True: ["/no/such/bold.ttf"]})
    for bold in (False, True):
        f = planting._font(20, bold)
        assert f.getname()[0] == "Bitstream Vera Sans" and planting._draws_spanish(f)
    from PIL import ImageFont
    assert not planting._draws_spanish(ImageFont.load_default(size=20))      # why the fallback is not enough
    # a font change gives the image a new name, so a cached image without accents is not reused
    with_vera = planting._font_id()
    monkeypatch.undo()
    monkeypatch.setattr(planting, "_bundled_font", lambda bold: None)
    monkeypatch.setattr(planting, "_SYSTEM_FONTS", {False: [], True: []})
    assert planting._font_id() != with_vera
