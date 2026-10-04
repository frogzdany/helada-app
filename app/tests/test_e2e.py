"""End-to-end simulator flow: alert -> voice note -> follow-up -> photos -> packet PDF -> audit."""
import io
from datetime import datetime

from pypdf import PdfReader

from backend.channels import twilio_signature
from backend.config import Settings
from backend.photo import make_exif_jpeg

PHONE = "+527220000001"      # P01, Aurelio Mendoza (ficticio), Almoloya de Juárez
P01 = (19.3351, -99.7923)   # ~0.9 km from SMN 15282 Tres Barrancas (station-anchored)


def texts(client, phone=PHONE):
    return [m for m in client.get("/api/sim/messages", params={"phone": phone}).json()]


def jpeg_bytes(tmp_path, i, when="2025-11-15T07:40"):
    p = make_exif_jpeg(tmp_path / f"f{i}.jpg", when=datetime.fromisoformat(when),
                       lat=P01[0] + 0.0003 * i, lon=P01[1])
    return p.read_bytes()


def test_full_sim_flow(client, tmp_path, settings, real_model):
    # 1) officer sends alerts for the demo night (replay of the real night 2025-11-14 -> 15)
    r = client.post("/api/alerts/send", json={"date": "2025-11-14", "scenario": "demo", "now": "2025-11-14T18:30"})
    assert r.status_code == 200 and r.json()["summary"]["sent"] >= 3
    fc = client.get("/api/forecast", params={"scenario": "demo"}).json()
    assert fc["model_source"] == "helada_model" and len(fc["rows"]) == 12 and fc["date"] == "2025-11-14"
    assert fc["replay"]["label"].startswith("Réplica de una noche real")
    assert any("Aviso de Helada" in (m["text"] or "") for m in texts(client))
    # 1b) the alert is short; answering "1" sends the rest
    r1 = client.post("/api/sim/inbound", data={"phone": PHONE, "text": "1", "now": "2025-11-14T18:35"})
    assert r1.status_code == 200
    assert any("Más sobre el aviso" in (m["text"] or "") for m in texts(client))

    # 2) farmer replies with a voice note (mock ASR reads the sidecar transcript)
    audio = tmp_path / "nota.ogg"
    audio.write_bytes(b"OggS-fake")
    side = settings.media_dir / "in"
    side.mkdir(parents=True, exist_ok=True)
    now = "2025-11-15T08:00"

    def post(**kw):
        data = {"phone": PHONE, "now": now, **{k: v for k, v in kw.items() if k != "files"}}
        return client.post("/api/sim/inbound", data=data, files=kw.get("files")).json()

    # voice note whose transcript lacks the area -> follow-up question about area
    import backend.asr as asr_mod
    orig = asr_mod.transcribe
    asr_mod.transcribe = lambda path, **k: asr_mod.Transcript("Se quemó la milpa anoche con la helada", "mock")
    try:
        out = post(files={"file": ("nota.ogg", audio.read_bytes(), "audio/ogg")})
    finally:
        asr_mod.transcribe = orig
    assert out["transcript"].startswith("Se quemó")
    assert out["slots"]["cause"] == "helada" and out["slots"]["date"] == "2025-11-14"
    assert "cuánto terreno" in out["replies"][0]["text"]

    # 3) answer the follow-up by text
    out = post(text="como una hectárea")
    assert out["replies"][0]["text"].startswith("Entendí: maíz de temporal, helada, 1 hectárea, el viernes 14 de noviembre.")
    assert "fotos" in out["replies"][1]["text"]

    # 4) four photos with EXIF near the parcel
    for i in range(4):
        out = post(files={"file": (f"f{i}.jpg", jpeg_bytes(tmp_path, i), "image/jpeg")})
    assert "pre-registro" in out["replies"][0]["text"]

    # 5) pre-registro not done -> packet says what is missing, never decides
    out = post(text="no")
    pk = out["packet"]
    assert pk and pk["status"] == "Falta: pre-registro PASACME"
    assert "Lo decide la Secretaría del Campo" in out["replies"][0]["text"]
    pdf = client.get(pk["url"])
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    text = " ".join(" ".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf.content)).pages).split())
    assert "Falta: pre-registro PASACME" in text and "elegib" not in text.lower()
    assert "Aviso enviado" in text                               # links back to the alert of that night
    assert "-3.0 °C — SMN 15282" in text                         # real observed Tmin of the replayed night
    assert "modelo entrenado sin la temporada 2025-26" in text   # replay never uses the in-sample shipped model

    lst = client.get("/api/packets").json()
    assert lst[0]["id"] == pk["id"] and lst[0]["summary"]["photos"] == 4
    # the farmer's phone received the PDF
    assert any(m["kind"] == "document" for m in texts(client))

    audit = client.get("/api/audit").json()
    assert audit["verify"]["ok"]
    actions = {e["action"] for e in audit["entries"]}
    assert {"alert.created", "alert.sent", "message.in", "report.slots", "packet.created"} <= actions


def test_optout_stops_alerts(client, real_model):
    client.post("/api/sim/inbound", data={"phone": PHONE, "text": "BAJA"})
    r = client.post("/api/alerts/send", json={"date": "2025-11-14", "scenario": "demo", "now": "2025-11-14T18:30",
                                              "parcel_ids": ["P01"]}).json()
    assert r["results"][0]["reason"] == "baja"


def test_unknown_number(client):
    out = client.post("/api/sim/inbound", data={"phone": "+525500000000", "text": "se heló"}).json()
    assert "padrón" in out["replies"][0]["text"]


def test_backtest_and_parcels(client):
    assert client.get("/api/backtest").json()["source"] == "TEMP_STUB"
    ps = client.get("/api/parcels").json()["parcels"]
    assert len(ps) == 12 and all(p["fictional"] for p in ps)
    assert {p["municipality"] for p in ps} == {"Almoloya de Juárez", "Temoaya", "Zinacantepec", "Ixtlahuaca",
                                               "San Felipe del Progreso", "Atlacomulco"}
    assert sum(1 for p in ps if p["near_station"]) == 10 and {p["parcel_id"] for p in ps if not p["near_station"]} == {"P05", "P10"}


def test_twilio_webhook_signature(tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    s = Settings(data_dir=tmp_path, offline=True, tts="off", asr="mock", llm_url="", scheduler=False,
                 channel="sim", twilio_token="secret123", twilio_validate=True, public_url="https://demo.example")
    with TestClient(create_app(s)) as c:
        params = {"From": "whatsapp:" + PHONE, "Body": "se heló media hectárea ayer", "NumMedia": "0"}
        bad = c.post("/webhooks/whatsapp", data=params, headers={"X-Twilio-Signature": "nope"})
        assert bad.status_code == 403
        sig = twilio_signature("secret123", "https://demo.example/webhooks/whatsapp", params)
        ok = c.post("/webhooks/whatsapp", data=params, headers={"X-Twilio-Signature": sig})
        assert ok.status_code == 200 and "<Response>" in ok.text
        msgs = c.get("/api/sim/messages", params={"phone": PHONE}).json()
        assert any(m["direction"] == "in" and m["channel"] == "twilio_whatsapp" for m in msgs)
        assert any(m["direction"] == "out" and "fotos" in (m["text"] or "") for m in msgs)


def test_demo_reset_replays_from_clean(client, real_model):
    """«▶ Demo» starts from a clean state: the same alerts go out again, and the audit chain is kept."""
    send = lambda: client.post("/api/alerts/send", json={"date": "2025-11-14", "scenario": "demo",
                                                         "now": "2025-11-14T18:30"}).json()["summary"]
    first = send()
    assert first["sent"] >= 3 and "sent" not in send()            # second press: already alerted that night
    client.post("/api/sim/inbound", data={"phone": "+527220000005", "text": "anoche marcó -2",
                                          "now": "2025-11-15T08:00"})
    n_audit = client.get("/api/audit").json()["verify"]["n"]

    r = client.post("/api/demo/reset")
    assert r.status_code == 200 and r.json()["cleared"]["alerts"] >= 3 and r.json()["cleared"]["logger_obs"] == 1
    assert texts(client) == [] and client.get("/api/alerts").json() == [] and client.get("/api/packets").json() == []
    audit = client.get("/api/audit").json()
    assert audit["verify"]["ok"] and audit["verify"]["n"] == n_audit + 1     # append-only: one more entry
    assert audit["entries"][0]["action"] == "demo.reset" or audit["entries"][-1]["action"] == "demo.reset"
    assert send()["sent"] == first["sent"]


def test_demo_reset_refused_on_a_real_channel(tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    s = Settings(data_dir=tmp_path, offline=True, tts="off", asr="mock", llm_url="", scheduler=False,
                 channel="twilio_whatsapp", twilio_sid="ACx", twilio_token="t")
    with TestClient(create_app(s)) as c:
        assert c.post("/api/demo/reset").status_code == 409


def test_json_says_utf8_and_accents_survive_the_round_trip(client):
    """«Entendí», «maíz», «hectárea» reach the page as written, and the header names the charset."""
    r = client.post("/api/sim/inbound", data={"phone": PHONE, "text": "se me heló el maíz anoche, hectárea y media",
                                              "now": "2025-11-15T08:00"})
    assert r.headers["content-type"] == "application/json; charset=utf-8"
    assert "Entendí: maíz de temporal, helada, hectárea y media".encode("utf-8") in r.content
    msgs = client.get("/api/sim/messages", params={"phone": PHONE})
    assert msgs.headers["content-type"] == "application/json; charset=utf-8"
    text = " ".join(m["text"] or "" for m in msgs.json())
    assert "Entendí" in text and "hectárea" in text and not any(bad in text for bad in ("Ã", "Â", "\ufffd"))


def test_simulated_phone_routes_are_closed_on_a_real_channel(settings):
    """With a real channel nobody can write as a farmer, or read a chat, through the simulator routes."""
    from fastapi.testclient import TestClient
    from backend.main import create_app
    app = create_app(settings)
    app.state.svc.channel.name = "twilio_whatsapp"
    with TestClient(app) as c:
        assert c.post("/api/sim/inbound", data={"phone": PHONE, "text": "hola"}).status_code == 409
        assert c.get("/api/sim/messages", params={"phone": PHONE}).status_code == 409
        assert c.post("/api/sim/sample", data={"phone": PHONE, "what": "photo"}).status_code == 409
        assert c.post("/api/demo/reset").status_code == 409
