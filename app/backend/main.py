"""Helada FastAPI app. Run: uv run uvicorn backend.main:app --reload (from app/)."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import random
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import model_adapter, roster, tts
from .channels import validate_twilio
from .config import STATIC_DIR, Settings
from .conversation import Inbound, handle_inbound
from .service import Helada

log = logging.getLogger("helada")
# a slim server image has no system MIME table: without these a voice note is served as octet-stream
for _ext, _type in ((".ogg", "audio/ogg"), (".opus", "audio/ogg"), (".m4a", "audio/mp4"), (".webm", "audio/webm")):
    mimetypes.add_type(_type, _ext)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

AUDIO_EXT = {"audio/ogg": ".ogg", "audio/opus": ".ogg", "audio/webm": ".webm", "video/webm": ".webm",
             "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/mpeg": ".mp3", "audio/wav": ".wav",
             "audio/x-wav": ".wav", "audio/amr": ".amr", "audio/aac": ".aac"}
IMAGE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}
MAX_UPLOAD_BYTES = 12_000_000   # one voice note or one phone photo; the page is reachable without a login


class UTF8JSONResponse(JSONResponse):
    """JSON is UTF-8 by definition; the header says so for clients that would otherwise read «Entendí» as Latin-1."""
    media_type = "application/json; charset=utf-8"


class SendAlertsBody(BaseModel):
    date: str | None = None
    parcel_ids: list[str] | None = None
    now: str | None = None
    override_window: bool = False
    scenario: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    svc = Helada(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if svc.s.scheduler:
            async def loop():
                while True:
                    try:
                        await asyncio.to_thread(svc.flush_queue)
                    except Exception as e:  # keep the loop alive
                        log.warning("flush_queue failed: %s", e)
                    await asyncio.sleep(60)
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Helada", version="0.1", lifespan=lifespan, default_response_class=UTF8JSONResponse)
    app.state.svc = svc
    for sub in ("media", "packets"):
        (svc.s.data_dir / sub).mkdir(parents=True, exist_ok=True)
        app.mount(f"/files/{sub}", StaticFiles(directory=svc.s.data_dir / sub), name=f"files-{sub}")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/movil")
    def movil():
        """The farmer's phone page: the model runs on the device, offline (static/movil)."""
        return RedirectResponse("/static/movil/index.html")

    # ------------------------------------------------------------ read APIs
    @app.get("/api/config")
    def config():
        return svc.config_info()

    @app.get("/api/parcels")
    def parcels():
        return {"note": "Nombres, teléfonos y superficies FICTICIOS; coordenadas reales del valle de Toluca.",
                "parcels": roster.all_parcels(svc.db)}

    @app.get("/api/forecast")
    def forecast(date: str | None = None, scenario: str | None = None):
        return svc.forecast(date, scenario)

    @app.get("/api/climatology/{parcel_id}")
    def climatology(parcel_id: str):
        p = roster.get_parcel(svc.db, parcel_id)
        if not p:
            raise HTTPException(404)
        return model_adapter.climatology(p)

    @app.get("/api/planting/{parcel_id}")
    def planting_advice(parcel_id: str):
        """«Calendario de siembra»: sowing windows per maize cycle from the parcel's frost climatology."""
        from . import planting
        p = roster.get_parcel(svc.db, parcel_id)
        if not p:
            raise HTTPException(404)
        return planting.advise(p)

    @app.get("/api/backtest")
    def backtest():
        return model_adapter.backtest_report()

    @app.get("/api/alerts")
    def list_alerts():
        return svc.list_alerts()

    @app.post("/api/alerts/send")
    def send_alerts(body: SendAlertsBody):
        return svc.send_alerts(body.date, parcel_ids=body.parcel_ids, now=body.now,
                               override_window=body.override_window, scenario=body.scenario)

    @app.post("/api/alerts/flush")
    def flush(now: str | None = None):
        return svc.flush_queue(now)

    @app.get("/api/packets")
    def packets():
        return svc.list_packets()

    @app.get("/api/packets/{packet_id}.pdf")
    def packet_pdf(packet_id: str):
        p = svc.packet_path(packet_id)
        if not p or not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="application/pdf", filename=f"{packet_id}.pdf")

    # ------------------------------------------------------------ «Registrador en la parcela»
    from . import parcel_logger as PL

    def _slim(cal: dict) -> dict:
        return {k: v for k, v in cal.items() if k != "site"}

    @app.get("/api/logger/demo")
    def logger_demo(until: str | None = None):
        return PL.demo_view(until)

    @app.get("/api/logger/{parcel_id}")
    def logger_get(parcel_id: str, date: str | None = None, scenario: str | None = None):
        p = roster.get_parcel(svc.db, parcel_id)
        if not p:
            raise HTTPException(404)
        out = PL.summary(svc, p)
        out["compare"] = PL.before_after(svc, parcel_id, date, scenario) if out["n_readings"] else None
        return out

    @app.post("/api/logger/{parcel_id}/readings")
    async def logger_add(parcel_id: str, text: str | None = Form(None), file: UploadFile | None = File(None),
                         date_is: str = Form("morning"), date: str | None = Form(None),
                         scenario: str | None = Form(None)):
        """Officer adds a parcel's nightly minima: CSV upload or pasted lines «fecha,tmin_c»."""
        p = roster.get_parcel(svc.db, parcel_id)
        if not p:
            raise HTTPException(404)
        if file is not None and file.filename:
            raw, source = (await file.read()).decode("utf-8-sig", errors="replace"), "csv"
        elif text:
            raw, source = text, "pegado"
        else:
            raise HTTPException(400, "suba un CSV o pegue lecturas «fecha,tmin_c»")
        if date_is not in ("morning", "evening"):
            raise HTTPException(400, "date_is debe ser morning o evening")
        parsed = PL.parse_table(raw, date_is)
        if not parsed["rows"]:
            return JSONResponse({"added": 0, "errors": parsed["errors"], "calibration": None, "compare": None}, 422)
        n = PL.add_readings(svc, parcel_id, parsed["rows"], source, raw_text=raw)
        cal = await asyncio.to_thread(PL.calibrate, svc, p)
        svc.audit.append("officer", "logger.calibrated", f"parcel:{parcel_id}", {
            "k": cal["k"], "readings": cal["n_readings"], "a_c": cal.get("a_c"), "b_c": cal.get("b_c"),
            "skipped": len(cal.get("skipped") or []), "via": source})
        cmp_ = await asyncio.to_thread(PL.before_after, svc, parcel_id, date, scenario)
        return {"added": n, "errors": parsed["errors"], "calibration": _slim(cal), "compare": cmp_, "curve": PL.curve()}

    @app.get("/api/audit")
    def audit(limit: int = 200):
        return {"verify": svc.audit.verify(), "entries": svc.audit.entries(limit)}

    # ------------------------------------------------------------ simulator
    def _save_upload(data: bytes, content_type: str, filename: str | None) -> tuple[Path, str]:
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"archivo demasiado grande (máximo {MAX_UPLOAD_BYTES // 1_000_000} MB)")
        ct = (content_type or "").split(";")[0].strip().lower()
        ext = Path(filename or "").suffix.lower()
        if ct in AUDIO_EXT or ext in (".ogg", ".opus", ".webm", ".m4a", ".mp3", ".wav", ".amr", ".aac"):
            kind, ext = "audio", AUDIO_EXT.get(ct, ext or ".ogg")
        elif ct in IMAGE_EXT or ext in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
            kind, ext = "image", IMAGE_EXT.get(ct, ext or ".jpg")
        else:
            raise HTTPException(415, f"tipo no soportado: {ct or ext}")
        d = svc.s.media_dir / "in"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{uuid.uuid4().hex[:12]}{ext}"
        path.write_bytes(data)
        return path, kind

    def _sim_only() -> None:
        """The simulated phone exists only on the simulator channel. With a real channel these routes would let
        anyone write as any farmer and read the conversations."""
        if svc.channel.name != "sim":
            raise HTTPException(409, "el teléfono simulado solo existe con el canal de simulación")

    @app.post("/api/sim/inbound")
    async def sim_inbound(phone: str = Form(...), text: str | None = Form(None),
                          lat: float | None = Form(None), lon: float | None = Form(None),
                          now: str | None = Form(None), file: UploadFile | None = File(None)):
        _sim_only()
        received = svc.now_local(now)
        if file is not None and file.filename:
            path, kind = _save_upload(await file.read(), file.content_type or "", file.filename)
            m = Inbound(phone, kind, text, path, received_at=received, channel="sim")
        elif lat is not None and lon is not None:
            m = Inbound(phone, "location", None, None, {"lat": lat, "lon": lon}, received, channel="sim")
        elif text:
            m = Inbound(phone, "text", text, received_at=received, channel="sim")
        else:
            raise HTTPException(400, "mande texto, archivo o ubicación")
        r = await asyncio.to_thread(handle_inbound, svc, m)
        return {"replies": r.replies, "transcript": r.transcript, "slots": r.slots,
                "packet": ({k: v for k, v in r.packet.items() if k != "checklist"} | {"status": r.packet["summary"]["status"]})
                if r.packet else None, "report_id": r.report_id}

    @app.get("/api/sim/messages")
    def sim_messages(phone: str, since_id: int = 0):
        _sim_only()
        return svc.messages(roster.normalize_phone(phone), since_id)

    @app.post("/api/sim/sample")
    async def sim_sample(phone: str = Form(...), what: str = Form(...), now: str | None = Form(None)):
        """Send a built-in sample as if the farmer sent it: what = audio | photo | location."""
        _sim_only()
        p = roster.parcel_by_phone(svc.db, phone)
        if not p:
            raise HTTPException(404, "teléfono no registrado")
        received = svc.now_local(now)
        if what == "location":
            m = Inbound(phone, "location", None, None, {"lat": p["lat"] + 0.0004, "lon": p["lon"] - 0.0003},
                        received, channel="sim")
        elif what == "photo":
            m = Inbound(phone, "image", None, _sample_photo(svc, p, received), received_at=received, channel="sim")
        elif what == "audio":
            path = _sample_audio(svc)
            if path is None:
                m = Inbound(phone, "text", SAMPLE_AUDIO_TEXT, received_at=received, channel="sim")
            else:
                m = Inbound(phone, "audio", None, path, received_at=received, channel="sim")
        else:
            raise HTTPException(400)
        r = await asyncio.to_thread(handle_inbound, svc, m)
        return {"replies": r.replies, "transcript": r.transcript, "slots": r.slots,
                "packet": {"id": r.packet["id"], "url": r.packet["url"], "status": r.packet["summary"]["status"]}
                if r.packet else None}

    @app.post("/api/demo/reset")
    def demo_reset():
        """Start the demo from a clean state (simulator only): see Helada.reset_demo."""
        try:
            return {"cleared": svc.reset_demo()}
        except PermissionError as e:
            raise HTTPException(409, str(e))

    # ------------------------------------------------------------ Twilio webhook
    @app.post("/webhooks/whatsapp")
    async def whatsapp_webhook(request: Request, background: BackgroundTasks):
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        if svc.s.twilio_validate:
            url = (svc.s.public_url + request.url.path) if svc.s.public_url else str(request.url)
            if not validate_twilio(svc.s.twilio_token, url, params, request.headers.get("X-Twilio-Signature")):
                raise HTTPException(403, "invalid Twilio signature")
        background.add_task(_process_twilio, svc, params)
        # Reply asynchronously via the REST API (ASR can exceed Twilio's 15 s webhook timeout).
        return Response('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="text/xml")

    return app


def _process_twilio(svc: Helada, params: dict) -> None:
    phone = roster.normalize_phone(params.get("From", ""))
    received = svc.now_local()
    try:
        n = int(params.get("NumMedia", "0") or 0)
        if n > 0:
            url, ct = params.get("MediaUrl0"), params.get("MediaContentType0", "")
            r = httpx.get(url, auth=(svc.s.twilio_sid, svc.s.twilio_token), follow_redirects=True, timeout=60)
            r.raise_for_status()
            ext = AUDIO_EXT.get(ct.split(";")[0], None) or IMAGE_EXT.get(ct.split(";")[0], ".bin")
            kind = "audio" if ct.startswith("audio") else "image" if ct.startswith("image") else "document"
            d = svc.s.media_dir / "in"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{uuid.uuid4().hex[:12]}{ext}"
            path.write_bytes(r.content)
            if kind == "document":
                return
            m = Inbound(phone, kind, params.get("Body") or None, path, received_at=received,
                        channel="twilio_whatsapp")
        elif params.get("Latitude") and params.get("Longitude"):
            m = Inbound(phone, "location", None, None, {"lat": float(params["Latitude"]),
                                                          "lon": float(params["Longitude"])}, received,
                        channel="twilio_whatsapp")
        else:
            m = Inbound(phone, "text", params.get("Body", ""), received_at=received, channel="twilio_whatsapp")
        handle_inbound(svc, m)
    except Exception as e:
        log.exception("twilio inbound failed: %s", e)


# ------------------------------------------------------------ demo samples
SAMPLE_AUDIO_TEXT = "Sí, se quemó la milpa, como una hectárea. Fue anoche, con la helada."


def _sample_audio(svc: Helada, recorded: bool = True) -> Path | None:
    d = svc.s.media_dir / "samples"
    d.mkdir(parents=True, exist_ok=True)
    out = d / "audio_ejemplo_aurelio.ogg"
    side = out.with_suffix(".ogg.txt")
    if not out.exists() and recorded and tts.SAMPLE_VOICE.exists():
        shutil.copy(tts.SAMPLE_VOICE, out)       # recorded ahead (scripts/make_demo_voice.py)
    if not out.exists():
        if not shutil.which("ffmpeg"):
            return None
        piper = svc.s.sample_piper_model or svc.s.piper_model     # on a server: a piper voice, not macOS `say`
        raw = d / "tmp.aiff"
        try:
            if shutil.which("say"):
                subprocess.run(["say", "-v", "Juan" if _has_voice("Juan") else svc.s.tts_voice, "-o", str(raw),
                                SAMPLE_AUDIO_TEXT], check=True, capture_output=True, timeout=60)
            elif shutil.which("piper") and piper and Path(piper).exists():
                raw = d / "tmp.wav"
                # no generator noise: the same audio every time (with it, about half the renderings of this
                # voice were misheard by whisper small: «sé que mola mi pa'» for «se quemó la milpa»)
                subprocess.run(["piper", "--model", piper, "--output_file", str(raw),
                                "--noise-scale", "0", "--noise-w-scale", "0"],
                               input=SAMPLE_AUDIO_TEXT.encode("utf-8"), check=True, capture_output=True, timeout=60)
            else:
                return None
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-ac", "1", "-ar", "16000",
                            "-c:a", "libopus", "-b:a", "24k", str(out)], check=True, capture_output=True, timeout=60)
        except Exception as e:
            log.warning("sample audio failed: %s", e)
            return None
        finally:
            raw.unlink(missing_ok=True)
    side.write_text(SAMPLE_AUDIO_TEXT, encoding="utf-8")   # transcript for the mock ASR
    # copy so each inbound message has its own file (and its own sidecar for mock ASR)
    dst = svc.s.media_dir / "in" / f"{uuid.uuid4().hex[:12]}.ogg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, dst)
    shutil.copy(side, dst.with_suffix(".ogg.txt"))
    return dst


def _has_voice(name: str) -> bool:
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
        return any(line.startswith(name + " ") for line in out.splitlines())
    except Exception:
        return False


def _sample_photo(svc: Helada, parcel: dict, when: datetime) -> Path:
    from PIL import Image, ImageDraw
    from .photo import make_exif_jpeg
    rnd = random.Random()
    im = Image.new("RGB", (800, 600), (170, 190, 215))
    dr = ImageDraw.Draw(im)
    dr.rectangle([0, 230, 800, 600], fill=(110, 85, 55))
    for i in range(38):
        x = 10 + i * 21 + rnd.randint(-4, 4)
        top = 150 + rnd.randint(0, 70)
        dr.line([x, 600, x + rnd.randint(-6, 6), top], fill=(150, 125, 70), width=4)
        for _ in range(3):
            y = rnd.randint(top + 20, 520)
            dr.line([x, y, x + rnd.choice([-1, 1]) * rnd.randint(20, 40), y - rnd.randint(10, 30)],
                    fill=rnd.choice([(120, 90, 50), (95, 70, 45), (160, 140, 80)]), width=3)
    dr.rectangle([0, 0, 800, 44], fill=(60, 30, 90))
    dr.text((14, 14), "FOTO DE EJEMPLO (sintetica) - Helada demo - " + parcel["parcel_id"], fill=(255, 255, 255))
    d = svc.s.media_dir / "in"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{uuid.uuid4().hex[:12]}.jpg"
    jitter = lambda: rnd.uniform(-0.0006, 0.0006)  # ~ +-65 m
    return make_exif_jpeg(path, when=(when - timedelta(minutes=rnd.randint(2, 30))).replace(tzinfo=None),
                          lat=parcel["lat"] + jitter(), lon=parcel["lon"] + jitter(), img=im)


app = create_app()
