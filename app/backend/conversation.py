"""Inbound flow: voice note / text / photo / location -> loss report -> evidence packet.

Every text / transcript first goes through the deterministic intent router (intents.classify):
opt-out / opt-in are handled first (consent), questions (forecast, planting, program) are answered without
opening or feeding a loss report, greetings and unrecognised text get the menu, and only a statement of
damage to the writer's own crop opens a report.

voice note -> ffmpeg -> ASR (Spanish) -> slot filler (LLM w/ JSON schema, or regex)
-> follow-up question for the first missing slot -> photos (>= 4, EXIF/GPS check)
-> pre-registro question -> PASACME checklist -> PDF packet -> reply.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date as Date, datetime, timedelta
from pathlib import Path

from . import alerts as A
from . import asr, intents, model_adapter, pasacme, photo, planting, roster, scenarios, smn_obs, tts
from .db import utcnow
from .packet import build_pdf
from .service import Helada, sha256_file
from .slots import fill_slots, intent, norm

log = logging.getLogger("helada.conversation")

REQUIRED = ("cause", "area_ha", "date")
MAX_ASKS = 2          # after asking the same slot twice, move on (packet will say "Falta: ...")
REPORT_TTL_DAYS = 14
PREREG_ASKS = ("preregistro", "preregistro_confirm")


@dataclass
class Inbound:
    phone: str
    kind: str                           # text | audio | image | location
    text: str | None = None
    media_path: Path | None = None
    location: dict | None = None        # {"lat":..,"lon":..}
    received_at: datetime | None = None
    channel: str = "sim"


@dataclass
class Result:
    replies: list[dict] = field(default_factory=list)
    transcript: str | None = None
    slots: dict | None = None
    packet: dict | None = None
    report_id: int | None = None
    planting: dict | None = None
    route: str | None = None            # intents.classify primary route of the text, if any


MORE_WORDS = {"1", "mas", "más", "uno", "mas info", "mas informacion", "quiero mas", "ver mas"}


def _templates() -> dict:
    return A.advisory()["templates"]


def _load_report(svc: Helada, phone: str, now: datetime) -> dict | None:
    r = svc.db.one("SELECT * FROM reports WHERE phone=? AND status='open' ORDER BY id DESC LIMIT 1", (phone,))
    if not r:
        return None
    if datetime.fromisoformat(r["updated_at"]) < now.astimezone() - timedelta(days=REPORT_TTL_DAYS):
        svc.db.execute("UPDATE reports SET status='cancelled' WHERE id=?", (r["id"],))
        return None
    r["state"] = json.loads(r["state"])
    return r


def _new_state() -> dict:
    return {"slots": {"crop": None, "area_ha": None, "cause": None, "date": None, "notes": None},
            "photos": [], "location": None, "asked": None, "asks": {}, "preregistro": None,
            "prereg_asked": False, "prereg_answered": False, "photos_done": False, "transcript": [], "slot_engine": None,
            "asr_engine": None}


def _save(svc: Helada, rep: dict) -> None:
    svc.db.execute("UPDATE reports SET state=?, updated_at=? WHERE id=?",
                   (json.dumps(rep["state"], ensure_ascii=False), utcnow(), rep["id"]))


def _create(svc: Helada, parcel: dict, phone: str) -> dict:
    st = _new_state()
    now = utcnow()
    rid = svc.db.execute("INSERT INTO reports(parcel_id, phone, status, state, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?)", (parcel["parcel_id"], phone, "open",
                                                  json.dumps(st, ensure_ascii=False), now, now))
    svc.audit.append("farmer", "report.opened", f"report:{rid}", {"parcel_id": parcel["parcel_id"]})
    return {"id": rid, "parcel_id": parcel["parcel_id"], "phone": phone, "status": "open", "state": st}


def handle_inbound(svc: Helada, m: Inbound) -> Result:
    phone = roster.normalize_phone(m.phone)
    now = m.received_at or svc.now_local()
    today = now.date()
    res = Result()
    parcel = roster.parcel_by_phone(svc.db, phone)
    meta: dict = {}
    if m.media_path:
        meta["sha256"] = sha256_file(m.media_path)
    if m.location:
        meta["location"] = m.location
    msg_id = svc.record_message(phone, parcel["parcel_id"] if parcel else None, "in", m.kind, m.text,
                                m.media_path, meta, channel=m.channel)
    svc.audit.append("farmer", "message.in", f"message:{msg_id}",
                     {"kind": m.kind, "phone": phone, "media_sha256": meta.get("sha256"), "channel": m.channel})

    def reply(text: str | None = None, media: Path | None = None):
        svc.send_out(phone, parcel["parcel_id"] if parcel else None, text=text, media_path=media)
        res.replies.append({"text": text, "media_url": svc.file_url(media) if media else None})

    if not parcel:
        reply("Este número no está en el padrón de Helada. Pida a su técnico que lo registre.")
        return res
    t = _templates()

    text = m.text
    asr_engine = None
    if m.kind == "audio" and m.media_path:
        try:
            tr = asr.transcribe(m.media_path, pref=svc.s.asr, whisper_model=svc.s.whisper_model)
        except Exception as e:
            log.warning("ASR failed: %s", e)
            tr = asr.Transcript(None, "error")
        asr_engine = tr.engine
        res.transcript = tr.text
        svc.db.execute("UPDATE messages SET meta=? WHERE id=?",
                       (json.dumps({**meta, "transcript": tr.text, "asr": tr.engine}, ensure_ascii=False), msg_id))
        if not tr.text:
            reply("No pude entender bien su audio. ¿Me lo puede repetir más despacio, o escribirlo?")
            return res
        text = tr.text

    area_over = False
    it = intent(text) if text else None          # short forms: listo / sí / no
    rep = _load_report(svc, phone, now)
    st = rep["state"] if rep else None
    asked = st["asked"] if st else None
    route = None
    if text and m.kind in ("text", "audio"):
        route = intents.classify(text, pending=asked if asked in ("crop", "area_ha", "cause", "date") else None,
                                 today=today, parcel_area=parcel.get("area_ha"))
        res.route = route.primary

    # ---- "1" / "más" right after an alert: the detail of the last alert (nothing pending in a loss report)
    if text and m.kind in ("text", "audio") and not asked and norm(text).strip(" .!¡?¿") in MORE_WORDS:
        a = svc.db.one("SELECT forecast FROM alerts WHERE parcel_id=? AND status='sent' ORDER BY id DESC LIMIT 1",
                       (parcel["parcel_id"],))
        detail = (json.loads(a["forecast"]).get("detail") if a else None)
        if detail:
            reply(detail)
            return res

    # ---- consent first: BAJA / ALTA in any wording, at any point of a conversation
    if route and route.optout:
        _set_optout(svc, parcel, phone, True, text)
        reply(t["optout"])
        if not route.damage:                     # "me tumbó la avena ... y dejen de mandarme avisos": both
            return res
    elif route and route.optin:
        _set_optout(svc, parcel, phone, False, text)
        reply(t["optin"])
        return res

    q_only = bool(route and route.questions and not route.damage)

    def answer_questions() -> None:
        for q in route.questions:
            {"forecast": lambda: _reply_forecast(svc, parcel, phone, now, res),
             "planting": lambda: _reply_planting(svc, parcel, phone, res),
             "program": lambda: _reply_program(svc, parcel, phone, res)}[q]()

    # ---- min-max thermometer reading («anoche marcó -2»): deterministic parse, confirm with sí/no, then store
    if text and m.kind in ("text", "audio") and not (route and (route.optout or route.optin)):
        from . import parcel_logger as PL
        pend = PL.pending(svc, phone, now)
        if pend is not None:
            if it in ("si", "no"):
                _resolve_reading(svc, parcel, pend, it == "si", reply)
                return res
            PL.resolve(svc, pend, False)            # anything else: the question lapses, nothing is stored
        reading = PL.parse_reading(text, today) if not (route and route.damage) else None
        if reading is not None:
            PL.propose(svc, parcel["parcel_id"], phone, reading, text)
            if route and route.questions:
                answer_questions()
            reply(f"¿Su termómetro marcó {A.temp_txt(reading['tmin_c'])} de mínima la noche del "
                  f"{A.noche(Date.fromisoformat(reading['night_date']))}? Responda «sí» para guardarlo en el "
                  "registro de su parcela, o «no».")
            return res

    if m.kind == "location" and m.location:
        if not rep:
            rep = _create(svc, parcel, phone)
            st = rep["state"]
        st["location"] = {"lat": float(m.location["lat"]), "lon": float(m.location["lon"]),
                          "at": now.isoformat(timespec="seconds")}
        d = roster.haversine_m(st["location"]["lat"], st["location"]["lon"], parcel["lat"], parcel["lon"])
        reply(f"Gracias, guardé su ubicación ({round(d)} m de su parcela registrada).")
    elif m.kind == "image" and m.media_path:
        if not rep:
            rep = _create(svc, parcel, phone)
            st = rep["state"]
        st["photos"].append({"path": str(m.media_path), "received_at": now.isoformat(timespec="seconds"),
                             "sha256": meta.get("sha256")})
    elif text:
        weather_q = q_only and ("forecast" in route.questions or "planting" in route.questions)
        if st and asked in PREREG_ASKS and not weather_q:
            ans = intents.read_preregistro(text)
            svc.audit.append("system", "report.preregistro", f"report:{rep['id']}",
                             {"answer": ans, "asked": asked, "text_sha256": hashlib.sha256(text.encode()).hexdigest()})
            if ans in ("si", "no"):
                st["preregistro"] = ans == "si"
                st["prereg_answered"] = True
            elif asked == "preregistro":         # unclear: ask once more, explaining what it is
                st["asked"] = "preregistro_confirm"
                _save(svc, rep)
                res.report_id = rep["id"]
                reply(t["followup"]["preregistro_confirm"])
                return res
            else:                                # still unclear -> packet says "Confirmar: pre-registro PASACME"
                st["preregistro"] = "unclear"
                st["prereg_answered"] = True
        elif st and it == "listo":
            st["photos_done"] = True
        elif q_only:                             # a question: answer it, never open or feed a loss report
            answer_questions()
            if rep and asked in t["followup"]:
                reply("Para seguir con su reporte: " + t["followup"][asked])
            return res
        elif not rep and not (route and route.damage):
            thanks = route is not None and route.greeting and "gracias" in norm(text)
            reply((t["thanks"] + " " if thanks else "") + t["help"])
            return res
        else:
            if route and route.questions:         # damage + a question: answer, then continue the report
                answer_questions()
            slots, engine = fill_slots(svc.filler, text, today, parcel.get("area_ha"), expected=asked)
            if _over_parcel(slots.get("area_ha"), parcel):     # «perdí 20 hectáreas» on a 2.5 ha parcel
                slots["area_ha"], area_over = None, True
            res.slots = slots
            if not rep:
                rep = _create(svc, parcel, phone)
                st = rep["state"]
            for k in ("crop", "area_ha", "cause", "date"):
                if slots.get(k) is not None:
                    st["slots"][k] = slots[k]
            st["transcript"].append(text)
            st["slots"]["notes"] = " / ".join(st["transcript"])[:500]
            st["slot_engine"] = engine
            if asr_engine:
                st["asr_engine"] = asr_engine
            svc.audit.append("system", "report.slots", f"report:{rep['id']}",
                             {"engine": engine, "asr": asr_engine, "route": route.primary if route else None,
                              "slots": {k: v for k, v in st["slots"].items() if k != "notes"}})
    if not rep:
        reply(t["help"])
        return res

    res.report_id = rep["id"]
    # ---------------- next step
    missing = [k for k in REQUIRED if st["slots"][k] is None and st["asks"].get(k, 0) < MAX_ASKS]
    min_photos = pasacme.rules()["min_photos"]
    n_ph = len(st["photos"])
    if not missing:
        # say back what was understood before asking for anything else; a correction is just another message
        resumen = _understood(st["slots"], today)
        if resumen and resumen != st.get("understood"):
            st["understood"] = resumen
            reply(t["followup"]["understood"].format(resumen=resumen))
    if missing:
        k = missing[0]
        st["asked"] = k
        st["asks"][k] = st["asks"].get(k, 0) + 1
        prefix = "Recibí su foto. " if m.kind == "image" else ""
        if k == "area_ha" and area_over:
            prefix = t["followup"]["area_over"].format(area=_area_dicha(float(parcel["area_ha"]))) + " "
        reply(prefix + t["followup"][k])
    elif n_ph == 0 and not st["photos_done"]:
        st["asked"] = "photo"
        reply(t["followup"]["photo"])
    elif n_ph < min_photos and not st["photos_done"]:
        st["asked"] = "photo"
        if m.kind == "image" or st["asks"].get("more_photos", 0) < 1:
            st["asks"]["more_photos"] = st["asks"].get("more_photos", 0) + 1
            reply(t["followup"]["more_photos"].format(n=n_ph, min=min_photos, faltan=min_photos - n_ph))
    elif not st["prereg_asked"]:
        st["prereg_asked"] = True
        st["asked"] = "preregistro"
        reply(t["followup"]["preregistro"])
    elif not st.get("prereg_answered") and m.kind in ("image", "location", "audio") and text is None:
        reply(t["followup"][st["asked"] if st["asked"] in PREREG_ASKS else "preregistro"])   # still waiting
    else:
        st["prereg_answered"] = True                 # answered (sí / no / "unclear" after the confirm question)
        st["asked"] = None
        _save(svc, rep)
        pk = make_packet(svc, parcel, rep, now)
        res.packet = pk
        limit = pk["summary"].get("notice_limit")
        plazo = f"antes del {A.fecha_larga(Date.fromisoformat(limit))}" if limit else "lo antes posible"
        reply(t["packet_ready"].format(estado=pk["summary"]["status"], plazo=plazo),
              media=Path(pk["pdf_path"]))
        return res
    _save(svc, rep)
    return res


def _understood(slots: dict, today: Date | None = None) -> str | None:
    """The report in one line, as the farmer would say it: «maíz de temporal, helada, media hectárea, el viernes 14
    de noviembre». Only what is known; None if nothing is. A date from another year says its year."""
    from .slots import CAUSE_LABEL, CROP_LABEL
    bits = []
    if slots.get("crop"):
        bits.append(CROP_LABEL.get(slots["crop"], slots["crop"]))
    if slots.get("cause"):
        bits.append(CAUSE_LABEL.get(slots["cause"], slots["cause"]))
    a = slots.get("area_ha")
    if a is not None:
        bits.append(_area_dicha(a))
    if slots.get("date"):
        try:
            d = Date.fromisoformat(slots["date"])
            bits.append("el " + A.fecha_larga(d) + (f" de {d.year}" if today and d.year != today.year else ""))
        except ValueError:
            pass
    return ", ".join(bits) if bits else None


def _over_parcel(area: float | None, parcel: dict) -> bool:
    """The damaged area cannot be larger than the registered parcel. Half a hectare of margin: farmers round
    («una hectárea» for a parcel of 0.8)."""
    reg = parcel.get("area_ha")
    return area is not None and bool(reg) and area > float(reg) + 0.5


def _area_dicha(a: float) -> str:
    """Area the way it is said in the field: «media hectárea», «hectárea y media», «2 hectáreas y media»."""
    if a == 0.5:
        return "media hectárea"
    if a == 1:
        return "1 hectárea"
    if a == 1.5:
        return "hectárea y media"
    if a > 1 and a % 1 == 0.5:
        return f"{int(a)} hectáreas y media"
    return f"{a:g} hectáreas"


def _resolve_reading(svc: Helada, parcel: dict, pend: dict, accept: bool, reply) -> None:
    """Farmer's sí/no to «¿Su termómetro marcó X?». Sí stores it (audit-logged) and recalibrates the parcel."""
    from . import parcel_logger as PL
    PL.resolve(svc, pend, accept)
    noche = A.noche(Date.fromisoformat(pend["night_date"]))
    if not accept:
        reply("No la guardé. Si quiere, mándela otra vez, por ejemplo: «anoche marcó -2».")
        return
    n = len(PL.observations(svc, parcel["parcel_id"]))
    k = n
    try:
        cal = PL.calibrate(svc, parcel)
        k = cal["k"] if cal.get("site") else n
        svc.audit.append("system", "logger.calibrated", f"parcel:{parcel['parcel_id']}",
                         {"k": k, "readings": n, "a_c": cal.get("a_c"), "b_c": cal.get("b_c"), "via": "whatsapp"})
    except Exception as e:  # the reading is stored either way
        log.warning("calibration after reading failed: %s", e)
    head = f"Guardado: {A.temp_txt(pend['tmin_c'])} la noche del {noche}. Su parcela lleva {n} noches registradas."
    if model_adapter.support_of_parcel(parcel) == "station-anchored":
        reply(head + " Su parcela ya se calcula con la estación del SMN de junto; guardamos su lectura para revisar el cálculo.")
        return
    now_p = PL.expected_precision(k)
    nxt = next((x for x in (7, 14, 30, 60, 90) if x > k), None)
    tail = (f" Error típico del pronóstico de su parcela: ±{now_p['mae_c']:.1f} °C"
            + (f"; con {nxt} noches, ±{PL.expected_precision(nxt)['mae_c']:.1f} °C." if nxt else ".")
            + " Mande su lectura cada mañana.")
    reply(head + tail)


def _set_optout(svc: Helada, parcel: dict, phone: str, out: bool, text: str) -> None:
    """Consent change by a word (BAJA/ALTA or any wording intents.py recognises). Logged in the audit chain;
    opting out also cancels alerts already queued for this phone, and service._deliver_alert re-checks it."""
    kind = "keyword" if (intents.OPTOUT_FULL if out else intents.OPTIN_FULL).fullmatch(norm(text)) else "phrase"
    svc.db.execute("INSERT INTO contacts(phone, opted_out) VALUES (?,?) "
                   "ON CONFLICT(phone) DO UPDATE SET opted_out=excluded.opted_out", (phone, 1 if out else 0))
    cancelled = []
    if out:
        for a in svc.db.all("SELECT a.id FROM alerts a JOIN parcels p ON p.parcel_id = a.parcel_id "
                            "WHERE a.status='queued' AND p.phone=?", (phone,)):
            svc.db.execute("UPDATE alerts SET status='cancelled', reason='baja' WHERE id=?", (a["id"],))
            svc.audit.append("system", "alert.cancelled", f"alert:{a['id']}", {"reason": "baja"})
            cancelled.append(a["id"])
    svc.audit.append("farmer", "contact.optout" if out else "contact.optin", f"phone:{phone}",
                     {"rule": kind, "parcel_id": parcel["parcel_id"], "cancelled_alerts": cancelled,
                      "text_sha256": hashlib.sha256(text.encode()).hexdigest()})


def _reply_forecast(svc: Helada, parcel: dict, phone: str, now: datetime, res: Result) -> None:
    """«¿Va a caer helada esta noche?»: tonight's forecast for the farmer's own parcel, and whether an alert
    was sent. Uses the alert already created for tonight when there is one (same numbers the farmer got),
    otherwise runs the model for tonight. Deterministic text from advisory.yaml; never opens a loss report."""
    T = _templates()["forecast"]
    pid = parcel["parcel_id"]
    night = now.date().isoformat()
    a = svc.db.one("SELECT * FROM alerts WHERE parcel_id=? AND night_date=? AND status IN ('sent','queued') "
                   "AND level != 'unsure' ORDER BY id DESC LIMIT 1", (pid, night))
    f, source, unsure = None, None, None
    if a:
        f = json.loads(a["forecast"])
        source = f.get("model_source")
    else:
        try:
            fc = svc.forecast(night, parcel_ids=[pid])
            if fc["rows"]:
                f, source, unsure = fc["rows"][0]["forecast"], fc["model_source"], fc["unsure_reason"]
        except Exception as e:  # the farmer still gets an honest answer
            log.warning("forecast reply failed: %s", e)
    if not f:
        text = T["unavailable"].format(**A.tecnico())
        level = None
    elif unsure or source == "TEMP_STUB":   # backup calculation or no forecast to run on: say so, give no number
        text = T["unsure"].format(parcel_id=pid, **A.tecnico())
        level, f = "unsure", None
    else:
        d = Date.fromisoformat(f["date"])
        fields = dict(parcel_id=pid, municipio=parcel.get("municipality", ""), noche=A.noche(d),
                      tmin_txt=A.temp_txt(f["tmin_c"]), lo_txt=A.temp_txt(f["tmin_lo_c"]),
                      hi_txt=A.temp_txt(f["tmin_hi_c"]), prob_voz=A.prob_voz(f["p_frost"]))
        level = a["level"] if a else A.level(f)
        if a and level == "alert":
            status = T["alert_sent"] if a["status"] == "sent" else T["alert_queued"]
        else:
            status = T.get(level or "none", T["none"])
        text = "\n".join([ln.format(**fields) for ln in T["text"]] + [status.format(**fields)])
    if svc._opted_out(phone):
        text += "\n" + T["optedout"]
    svc.send_out(phone, pid, text=text, meta={"forecast_reply": True})
    res.replies.append({"text": text, "media_url": None})
    svc.audit.append("system", "forecast.reply", f"parcel:{pid}",
                     {"night_date": night, "level": level, "p_frost": f["p_frost"] if f else None,
                      "from_alert": a["id"] if a else None, "model_source": source, "unsure_reason": unsure,
                      "text_sha256": hashlib.sha256(text.encode()).hexdigest()})


def _reply_program(svc: Helada, parcel: dict, phone: str, res: Result) -> None:
    """Questions about the PASACME program: the fixed facts from pasacme.yaml (10-day notice, pre-registro,
    documents, on-site inspection, 60% threshold). Never says whether the farmer qualifies."""
    R = pasacme.rules()
    text = _templates()["program"].format(
        delegacion=R["delegaciones"].get(parcel.get("municipality", ""), "de su región"),
        dias=R["notice_days"], pct=R["min_damage_pct"])
    pasacme.assert_no_eligibility(text)
    svc.send_out(phone, parcel["parcel_id"], text=text, meta={"program_info": True})
    res.replies.append({"text": text, "media_url": None})


def _reply_planting(svc: Helada, parcel: dict, phone: str, res: Result) -> None:
    """«¿Cuándo siembro?»: short text + voice note + table image, from the parcel's frost climatology
    (deterministic; does not touch an open loss report)."""
    adv = planting.advise(parcel)
    img = None
    try:
        img = planting.render_png(parcel, adv, svc.s.media_dir / "siembra")
    except Exception as e:  # the text carries the same numbers
        log.warning("planting image failed: %s", e)
    audio = tts.synthesize(adv["voice"], svc.s.media_dir / "tts", pref=svc.s.tts, voice=svc.s.tts_voice,
                           piper_model=svc.s.piper_model)
    svc.send_out(phone, parcel["parcel_id"], text=adv["text"], audio_path=audio, media_path=img,
                 sms_text=adv["sms"], meta={"planting": True})
    res.replies.append({"text": adv["text"], "media_url": svc.file_url(img) if img else None,
                        "audio_url": svc.file_url(audio) if audio else None})
    res.planting = {k: adv[k] for k in ("parcel_id", "basis", "frost", "recommendation", "support")}
    svc.audit.append("system", "planting.advice", f"parcel:{parcel['parcel_id']}",
                     {"climatology_source": adv["climatology_source"], "support": adv["support"],
                      "latest_p10": {c["id"]: c["latest_p10"] for c in adv["cycles"]},
                      "signed": adv["signed"],
                      "text_sha256": hashlib.sha256(adv["text"].encode()).hexdigest()})


def _weather(svc: Helada, parcel: dict, event_date: str | None, cause: str | None) -> dict:
    if not event_date:
        return {"summary": "Sin fecha del evento"}
    a = svc.db.one("SELECT * FROM alerts WHERE parcel_id=? AND night_date IN (?, ?) AND status='sent' "
                   "AND level != 'unsure' ORDER BY id DESC LIMIT 1",
                   (parcel["parcel_id"], event_date,
                    (Date.fromisoformat(event_date) - timedelta(days=1)).isoformat()))
    f = None
    alert_line = None
    if a:
        f = json.loads(a["forecast"])
        kind = "Vigilancia (no aviso) enviada" if a.get("level") == "watch" else "Aviso enviado"
        alert_line = (f"{kind} por {a['channel']} el {a['sent_at'][:16].replace('T', ' ')} (hora local) "
                      f"para la noche {a['night_date']}")
    else:
        try:
            r = model_adapter.predict([parcel], event_date, None, offline=svc.s.offline, cache=svc.db)
            f = r["forecasts"][0] if r["forecasts"] else None
            if f:
                f["model_source"] = r["source"]
                f["grid_source"] = r["grid"].get(parcel["parcel_id"], {}).get("source")
                if model_adapter.is_fallback(f["grid_source"]):
                    f = None     # no forecast for that night, only a monthly average: the packet shows no model number
        except Exception as e:
            log.warning("weather lookup failed: %s", e)

    def c(x):
        return f"{x:+.1f} °C".replace("+-", "-")
    if not f:
        obs_txt, obs_status = _station_obs(parcel, event_date, False, c)
        return {"summary": f"Sin dato del modelo; estación SMN: {obs_txt}", "alert_line": alert_line,
                "station_obs": obs_txt, "station_obs_status": obs_status}
    demo = f.get("scenario") == "demo"
    if demo:
        grid_src = "réplica de una noche real: pronóstico archivado Open-Meteo ecmwf_ifs025, día 1"
    else:
        grid_src = "Open-Meteo, celda sin ajuste" + (f", {f['grid_source']}" if f.get("grid_source") else "")
    sup = A.support(f)
    sup_txt = {"station-anchored": "soporte: anclada a estación SMN (régimen validado)",
               "terrain-transfer": "soporte: transferencia por terreno, sin estación cercana (rango amplio; "
                                   "precondición: registrador en la parcela)"}.get(sup, "soporte: sin dato")
    out = {
        "parcel_line": f"{c(f['tmin_c'])} (80%: {c(f['tmin_lo_c'])} a {c(f['tmin_hi_c'])}); "
                       f"P(helada) = {round(100 * f['p_frost'])}% — noche {f['date']}; {sup_txt}",
        "grid_line": f"{c(f['grid_tmin_c'])} ({grid_src})",
        "alert_line": alert_line or "Sin aviso registrado para esa noche",
    }
    obs_txt, out["station_obs_status"] = _station_obs(parcel, f.get("date") or event_date, demo, c)
    out["station_obs"] = obs_txt
    if f.get("model_variant_label"):
        out["model_note"] = f["model_variant_label"]
    out["summary"] = (f"Tmin modelada {c(f['tmin_c'])}, P(helada) {round(100 * f['p_frost'])}%, "
                      f"pronóstico regional {c(f['grid_tmin_c'])}; estación SMN: {obs_txt}")
    return out


def _station_obs(parcel: dict, night_date: str, demo: bool, c) -> tuple[str, str]:
    """Observed Tmin at the nearest SMN station for the night starting on `night_date` (evening date).
    Real SMN value when published; otherwise an honest "not published yet (typical lag ...)" line."""
    ns = roster.nearest_station(parcel["lat"], parcel["lon"])
    o = smn_obs.observed_for_night(ns["station"], night_date)
    name = f"SMN {ns['station']} {ns['name']}"
    far = (f"; la estación está a {ns['distance_km']} km, la parcela puede diferir"
           if ns["distance_km"] > 1.5 else "")
    if o["status"] != "ok":
        return f"{o['text']} — {name}{far}", o["status"]
    tag = "dato real; réplica de demostración" if demo else "dato real del archivo diario SMN"
    txt = f"{c(o['tmin_c'])} — {name}, lectura de las 08:00 del {o['obs_date']} ({tag}{far})"
    if o["prev_tmin_c"] is not None:
        txt += f". Lectura del {o['prev_date']} (noche anterior): {c(o['prev_tmin_c'])}"
    return txt, "ok"


def make_packet(svc: Helada, parcel: dict, rep: dict, now: datetime) -> dict:
    st = rep["state"]
    s = st["slots"]
    today = now.date()
    # (re)check every photo against the final event date and shared location
    photos = []
    for ph in st["photos"]:
        v = photo.check(Path(ph["path"]), parcel, s.get("date"), datetime.fromisoformat(ph["received_at"]),
                        st.get("location"))
        photos.append({"path": ph["path"], "verdict": v, "status": v["status"]})
    year = Date.fromisoformat(s["date"]).year if s.get("date") else today.year
    prior = svc.db.one("SELECT COUNT(*) n FROM packets WHERE parcel_id=? AND year=?",
                       (parcel["parcel_id"], year))["n"]
    weather = _weather(svc, parcel, s.get("date"), s.get("cause"))
    report_view = {"slots": s, "photos": [{"status": p["status"]} for p in photos],
                   "preregistro": st.get("preregistro"), "slot_engine": st.get("slot_engine"),
                   "stage_declared": intents.read_stage(". ".join(st["transcript"]))
                   if (s.get("crop") or parcel.get("crop")) == "maiz_temporal" else None,
                   "asr_engine": st.get("asr_engine")}
    cl = pasacme.build_checklist(parcel, report_view, today, prior_packets_this_year=prior, weather=weather)
    n = svc.db.one("SELECT COUNT(*) n FROM packets")["n"] + 1
    packet_id = f"HEL-{year}-{parcel['parcel_id']}-{n:03d}"
    pdf_path = svc.s.packets_dir / f"{packet_id}.pdf"
    head = svc.audit.head()
    sha = build_pdf(pdf_path, packet_id=packet_id, parcel=parcel, report=report_view, checklist=cl,
                    weather=weather, station=roster.nearest_station(parcel["lat"], parcel["lon"]),
                    photos=photos, transcript=" / ".join(st["transcript"]), audit_head=head,
                    model_source=model_adapter.effective_source()
                    + (f" — {weather['model_note']}" if weather.get("model_note") else ""), generated_at=now)
    notice_limit = None
    if s.get("date"):
        notice_limit = (Date.fromisoformat(s["date"]) + timedelta(days=pasacme.rules()["notice_days"])).isoformat()
    summary = {"status": cl["status"], "complete": cl["complete"], "missing": cl["missing"],
               "confirm": cl["confirm"],
               "review": cl["review"], "owner_name": parcel["owner_name"], "municipality": parcel["municipality"],
               "cause": s.get("cause"), "date": s.get("date"), "area_ha": s.get("area_ha"),
               "photos": len(photos), "notice_limit": notice_limit, "delegacion": cl["delegacion"]}
    svc.db.execute("INSERT INTO packets(id, parcel_id, report_id, year, pdf_path, sha256, summary, created_at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (packet_id, parcel["parcel_id"], rep["id"], year, str(pdf_path),
                                                sha, json.dumps(summary, ensure_ascii=False), utcnow()))
    svc.db.execute("UPDATE reports SET status='packet', updated_at=? WHERE id=?", (utcnow(), rep["id"]))
    svc.audit.append("system", "packet.created", f"packet:{packet_id}",
                     {"report_id": rep["id"], "pdf_sha256": sha, "status": cl["status"],
                      "photos_sha256": [ph.get("sha256") for ph in st["photos"]]})
    return {"id": packet_id, "pdf_path": str(pdf_path), "sha256": sha, "summary": summary,
            "checklist": cl, "url": f"/api/packets/{packet_id}.pdf"}
