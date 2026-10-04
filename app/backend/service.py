"""Application service: forecasts, alert sending, outbound/inbound messages, packets."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from datetime import date as Date, datetime, timedelta
from pathlib import Path

from . import alerts as A
from . import model_adapter, roster, smn_obs, tts
from .audit import AuditLog
from .channels import OutMsg, make_channel
from .config import Settings
from .db import DB, utcnow
from . import scenarios
from .slots import make_filler

log = logging.getLogger("helada.service")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Helada:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or Settings()
        self.s.ensure_dirs()
        self._copy_seed()
        self.db = DB(self.s.db_path)
        self.audit = AuditLog(self.db)
        roster.seed(self.db)
        self.filler = make_filler(self.s.llm_url, self.s.llm_model)
        self.channel = make_channel(self.s)
        if not self.db.one("SELECT seq FROM audit LIMIT 1"):
            self.audit.append("system", "system.start", None, {
                "model_installed": model_adapter.MODEL_SOURCE == "helada_model",
                "channel": self.channel.name, "note": "genesis entry"})

    def _copy_seed(self) -> None:
        """Voice notes made when the image was built (the replay night's alerts, the farmer's sample): a slow
        server CPU then never synthesizes them inside a request. Files already in the data dir are kept."""
        seed = Path(self.s.seed_dir) if self.s.seed_dir else None
        if not seed or not seed.is_dir():
            return
        for src in seed.rglob("*"):
            dst = self.s.media_dir / src.relative_to(seed)
            if src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst)

    # ------------------------------------------------------------ time
    def now_local(self, now: str | datetime | None = None) -> datetime:
        if isinstance(now, datetime):
            return now if now.tzinfo else now.replace(tzinfo=self.s.tz)
        if isinstance(now, str) and now:
            d = datetime.fromisoformat(now)
            return d if d.tzinfo else d.replace(tzinfo=self.s.tz)
        return datetime.now(self.s.tz)

    def today(self, now=None) -> Date:
        return self.now_local(now).date()

    # ------------------------------------------------------------ forecast
    def forecast(self, night_date: str | None = None, scenario: str | None = None,
                 parcel_ids: list[str] | None = None, use_loggers: bool = True) -> dict:
        demo = scenario == "demo"
        # The demo is a replay of a real night: its forecast values belong to that date, so the date is fixed.
        night_date = scenarios.demo_night() if demo else (night_date or self.today().isoformat())
        parcels = roster.all_parcels(self.db)
        if parcel_ids:
            parcels = [p for p in parcels if p["parcel_id"] in parcel_ids]
        fc_in = scenarios.demo_forecast(parcels) if demo else None
        sites = None
        if use_loggers and model_adapter.effective_source() == "helada_model":
            from . import parcel_logger
            sites = parcel_logger.site_calibrations(self, parcels) or None
        res = model_adapter.predict(parcels, night_date, fc_in, offline=self.s.offline, cache=self.db,
                                    variant=model_adapter.HOLDOUT_VARIANT if demo else None, sites=sites)
        by_id = {p["parcel_id"]: p for p in parcels}
        rows = []
        for f in res["forecasts"]:
            p = by_id[f["parcel_id"]]
            g = res["grid"].get(f["parcel_id"], {})
            row = {"parcel": p, "forecast": f, "grid": g, "triggers": A.triggers(f), "level": A.level(f)}
            if demo:
                row["truth"] = scenarios.demo_truth(p["parcel_id"])
            else:
                row["observed"] = self.observed(p, f["date"])
            rows.append(row)
        sources = sorted({r["grid"].get("source", "?") for r in rows})
        # Fail-safe: no validated number for this night -> no alert and no figure reaches a farmer (send_alerts,
        # conversation._reply_forecast). Either the model did not run, or there was no forecast to feed it.
        unsure = ("sin_modelo" if res["source"] == "TEMP_STUB"
                  else "sin_pronostico" if any(model_adapter.is_fallback(x) for x in sources) else None)
        return {"date": night_date, "scenario": scenario or "live", "model_source": res["source"],
                "unsure_reason": unsure,
                "model_variant": res.get("variant"), "model_variant_label": res.get("variant_label"),
                "grid_sources": sources, "rows": rows,
                "replay": scenarios.replay_info() if demo else None,
                "rule": {"alert": "p_frost >= 0.30", "watch": "terrain-transfer and tmin_lo_c <= 0 and p_frost < 0.30",
                         "p_frost_min": A.P_FROST_MIN, "watch_tmin_lo_max": A.TMIN_LO_MAX,
                         "max_per_7d": A.MAX_PER_7D, "window": "18:00-20:00"}}

    def observed(self, parcel: dict, night_date: str) -> dict | None:
        """SMN observed Tmin for the night at the station next to the parcel (only if <= 1.5 km: otherwise the
        station does not stand for the parcel). Status says when the SMN has not published it yet."""
        ns = roster.nearest_station(parcel["lat"], parcel["lon"])
        if ns["distance_km"] > 1.5:
            return None
        o = smn_obs.observed_for_night(ns["station"], night_date)
        return {"status": o["status"], "tmin_c": o["tmin_c"], "obs_date": o["obs_date"], "station": ns["station"],
                "station_name": ns["name"], "text": o.get("text"), "last_date": o.get("last_date")}

    # ------------------------------------------------------------ alerts
    def _history(self, parcel_id: str, now_local: datetime) -> list[datetime]:
        rows = self.db.all("SELECT created_at FROM alerts WHERE parcel_id=? AND status IN ('sent','queued') "
                           "AND level='alert'", (parcel_id,))   # watches do not count toward the weekly cap
        return [datetime.fromisoformat(r["created_at"]).astimezone(self.s.tz) for r in rows]

    def _opted_out(self, phone: str) -> bool:
        r = self.db.one("SELECT opted_out FROM contacts WHERE phone=?", (phone,))
        return bool(r and r["opted_out"])

    def send_alerts(self, night_date: str | None = None, *, parcel_ids: list[str] | None = None,
                    now: str | datetime | None = None, override_window: bool = False,
                    scenario: str | None = None, with_audio: bool = True) -> dict:
        now_l = self.now_local(now)
        fc = self.forecast(night_date, scenario, parcel_ids)
        no_model = fc["unsure_reason"]     # "sin_modelo" | "sin_pronostico" | None
        results = []
        tts_spent = 0.0        # seconds of synthesis in this send (a cached voice note costs nothing)
        for row in fc["rows"]:
            p, f = row["parcel"], row["forecast"]
            existing = self.db.one("SELECT id, status FROM alerts WHERE parcel_id=? AND night_date=? "
                                   "AND status IN ('sent','queued') AND level != 'unsure' ORDER BY id DESC LIMIT 1",
                                   (p["parcel_id"], f["date"]))
            if existing and existing["status"] == "queued" and (override_window or A.in_window(now_l)):
                results.append(self._deliver_alert(existing["id"], now_l))
                continue
            if no_model:     # no validated number for tonight (backup calculation, or no forecast): none reaches a farmer
                results.append(self._unsure_notice(p, f["date"], now_l, already_alerted=existing is not None,
                                                   override_window=override_window, with_audio=with_audio,
                                                   why=no_model))
                continue
            d = A.decide(f, self._history(p["parcel_id"], now_l), now_l,
                         already_for_date=existing is not None, opted_out=self._opted_out(p["phone"]),
                         override_window=override_window)
            res = {"parcel_id": p["parcel_id"], **d.as_dict(), "p_frost": f["p_frost"], "tmin_c": f["tmin_c"]}
            if d.action in ("send", "queue"):
                watch = d.level == "watch"
                msg = A.render_watch(p, f, now_l) if watch else A.render(p, f, now_l)
                audio = None
                # a WATCH never gets a voice note; past the budget the alert still goes out, as text
                if with_audio and not watch and (not self.s.tts_budget_s or tts_spent < self.s.tts_budget_s):
                    t0 = time.monotonic()
                    audio = tts.synthesize(msg["voice"], self.s.media_dir / "tts", pref=self.s.tts,
                                           voice=self.s.tts_voice, piper_model=self.s.piper_model)
                    tts_spent += time.monotonic() - t0
                aid = self.db.execute(
                    "INSERT INTO alerts(parcel_id, night_date, status, reason, text, sms_text, audio_path, channel, "
                    "forecast, created_at, level) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (p["parcel_id"], f["date"], "queued", d.reason, msg["text"], msg["sms"],
                     str(audio) if audio else None, self.channel.name,
                     json.dumps({**f, "stage": msg["stage"], "sources": msg["sources"],
                                 "model_source": fc["model_source"], "scenario": fc["scenario"],
                                 "model_variant": fc["model_variant"], "model_variant_label": fc["model_variant_label"],
                                 "level": d.level, "voice": msg["voice"], "detail": msg.get("detail")}, ensure_ascii=False),
                     now_l.isoformat(timespec="seconds"), d.level))
                self.audit.append("officer", "alert.created", f"alert:{aid}", {
                    "parcel_id": p["parcel_id"], "night_date": f["date"], "decision": d.as_dict(),
                    "override_window": override_window, "scenario": fc["scenario"],
                    "model_source": fc["model_source"], "model_variant": fc["model_variant"], "level": d.level,
                    "grid_source": row["grid"].get("source"), "p_frost": f["p_frost"], "tmin_c": f["tmin_c"],
                    "tmin_lo_c": f["tmin_lo_c"], "text_sha256": hashlib.sha256(msg["text"].encode()).hexdigest()})
                if d.action == "send":
                    res.update(self._deliver_alert(aid, now_l))
                else:
                    res["alert_id"] = aid
            results.append(res)
        summary = {}
        for r in results:
            key = r.get("status") or r.get("reason")
            if r.get("level") == "watch":
                key = "vigilancia" if key == "sent" else "vigilancia_" + key
            elif r.get("level") == "unsure" and key == "sent":
                key = "no_seguro"
            summary[key] = summary.get(key, 0) + 1
        if no_model:
            self.audit.append("system", "alert.skipped", None, {
                "reason": no_model, "night_date": fc["date"], "parcels": len(results), "summary": summary,
                "grid_sources": fc["grid_sources"], "model_error": model_adapter.LAST_ERROR})
        return {"date": fc["date"], "scenario": fc["scenario"], "model_source": fc["model_source"],
                "unsure_reason": no_model, "grid_sources": fc["grid_sources"],
                "now": now_l.isoformat(timespec="minutes"), "in_window": A.in_window(now_l),
                "summary": summary, "results": results}

    def _unsure_notice(self, p: dict, night: str, now_l: datetime, *, already_alerted: bool,
                       override_window: bool, with_audio: bool, why: str = "sin_modelo") -> dict:
        """No validated number for this night (`why`: the model did not run, or there was no forecast to feed it):
        instead of an alert, one «no estoy seguro, pregunte a su técnico» notice with no numbers. Never queued
        (by the evening window the model or the forecast may be back)."""
        pid = p["parcel_id"]
        res = {"parcel_id": pid, "action": "skip", "reason": why, "triggered": False, "level": "unsure"}
        told = self.db.all("SELECT night_date, created_at FROM alerts WHERE parcel_id=? AND level='unsure' "
                           "AND status='sent'", (pid,))
        week = [datetime.fromisoformat(r["created_at"]).astimezone(self.s.tz) for r in told]
        if self._opted_out(p["phone"]):
            res["reason"] = "baja"
        elif already_alerted or any(r["night_date"] == night for r in told):
            res["reason"] = "ya_avisado"
        elif A.count_in_last_7d(week, now_l) >= A.MAX_UNSURE_PER_7D:
            res["reason"] = "tope_semanal"
        elif override_window or A.in_window(now_l):
            msg = A.render_unsure(p, now_l)
            audio = None
            if with_audio:
                audio = tts.synthesize(msg["voice"], self.s.media_dir / "tts", pref=self.s.tts,
                                       voice=self.s.tts_voice, piper_model=self.s.piper_model)
            aid = self.db.execute(
                "INSERT INTO alerts(parcel_id, night_date, status, reason, text, sms_text, audio_path, channel, "
                "forecast, created_at, level) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, night, "queued", why, msg["text"], msg["sms"], str(audio) if audio else None,
                 self.channel.name, json.dumps({"date": night, "level": "unsure", "unsure_reason": why,
                                                "model_source": "TEMP_STUB" if why == "sin_modelo" else None,
                                                "voice": msg["voice"]}, ensure_ascii=False),
                 now_l.isoformat(timespec="seconds"), "unsure"))
            self.audit.append("system", "alert.unsure", f"alert:{aid}", {
                "parcel_id": pid, "night_date": night, "reason": why,
                "text_sha256": hashlib.sha256(msg["text"].encode()).hexdigest()})
            res.update(self._deliver_alert(aid, now_l))
        return res

    def _deliver_alert(self, alert_id: int, now_l: datetime) -> dict:
        a = self.db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))
        p = roster.get_parcel(self.db, a["parcel_id"])
        if self._opted_out(p["phone"]):          # opted out after it was queued: never send it
            self.db.execute("UPDATE alerts SET status='cancelled', reason='baja' WHERE id=?", (alert_id,))
            self.audit.append("system", "alert.cancelled", f"alert:{alert_id}", {"reason": "baja",
                                                                                "parcel_id": p["parcel_id"]})
            return {"parcel_id": p["parcel_id"], "alert_id": alert_id, "status": "cancelled", "action": "skip",
                    "reason": "baja", "level": a.get("level", "alert")}
        audio = Path(a["audio_path"]) if a["audio_path"] else None
        dres = self.send_out(p["phone"], p["parcel_id"], text=a["text"], audio_path=audio,
                             sms_text=a["sms_text"], meta={"alert_id": alert_id})
        status = "sent" if dres.get("status") not in ("failed",) else "failed"
        self.db.execute("UPDATE alerts SET status=?, sent_at=?, provider_id=? WHERE id=?",
                        (status, now_l.isoformat(timespec="seconds"), dres.get("provider_id"), alert_id))
        self.audit.append("system", "alert." + status, f"alert:{alert_id}", {
            "parcel_id": p["parcel_id"], "channel": self.channel.name, "provider_id": dres.get("provider_id"),
            "audio_sha256": sha256_file(audio) if audio and audio.exists() else None})
        return {"parcel_id": p["parcel_id"], "alert_id": alert_id, "status": status, "action": "send",
                "reason": a["reason"], "level": a.get("level", "alert")}

    def flush_queue(self, now: str | datetime | None = None) -> list[dict]:
        """Deliver queued alerts when inside the evening window; expire stale ones."""
        now_l = self.now_local(now)
        out = []
        for a in self.db.all("SELECT id, night_date FROM alerts WHERE status='queued'"):
            if Date.fromisoformat(a["night_date"]) < now_l.date():
                self.db.execute("UPDATE alerts SET status='expired' WHERE id=?", (a["id"],))
                self.audit.append("system", "alert.expired", f"alert:{a['id']}", {})
            elif A.in_window(now_l):
                out.append(self._deliver_alert(a["id"], now_l))
        return out

    def list_alerts(self, limit: int = 200) -> list[dict]:
        rows = self.db.all("SELECT id, parcel_id, night_date, status, level, reason, text, sms_text, audio_path, "
                           "channel, created_at, sent_at FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["audio_url"] = self.file_url(r.pop("audio_path"))
        return rows

    # ------------------------------------------------------------ messages
    def file_url(self, path: str | Path | None) -> str | None:
        if not path:
            return None
        try:
            rel = Path(path).resolve().relative_to(self.s.data_dir.resolve())
            return "/files/" + rel.as_posix()
        except ValueError:
            return None

    def record_message(self, phone: str, parcel_id: str | None, direction: str, kind: str, text: str | None,
                       media_path: Path | None = None, meta: dict | None = None, channel: str | None = None) -> int:
        return self.db.execute(
            "INSERT INTO messages(phone, parcel_id, direction, kind, text, media_path, meta, channel, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (phone, parcel_id, direction, kind, text, str(media_path) if media_path else None,
             json.dumps(meta or {}, ensure_ascii=False), channel or self.channel.name, utcnow()))

    def send_out(self, phone: str, parcel_id: str | None, *, text: str | None = None,
                 audio_path: Path | None = None, media_path: Path | None = None, sms_text: str | None = None,
                 meta: dict | None = None) -> dict:
        if text:
            self.record_message(phone, parcel_id, "out", "text", text, meta=meta)
        if audio_path:
            self.record_message(phone, parcel_id, "out", "audio", None, audio_path, meta=meta)
        if media_path:
            kind = "document" if str(media_path).endswith(".pdf") else "image"
            self.record_message(phone, parcel_id, "out", kind, Path(media_path).name, media_path, meta=meta)
        try:
            return self.channel.send(OutMsg(phone, text, audio_path, media_path, sms_text))
        except Exception as e:
            log.warning("channel send failed: %s", e)
            return {"status": "failed", "error": str(e)}

    def messages(self, phone: str, since_id: int = 0) -> list[dict]:
        rows = self.db.all("SELECT * FROM messages WHERE phone=? AND id>? ORDER BY id", (phone, since_id))
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
            r["media_url"] = self.file_url(r.pop("media_path"))
        return rows

    # ------------------------------------------------------------ packets
    def list_packets(self) -> list[dict]:
        rows = self.db.all("SELECT * FROM packets ORDER BY created_at DESC")
        for r in rows:
            r["summary"] = json.loads(r["summary"])
            r["url"] = f"/api/packets/{r['id']}.pdf"
            r.pop("pdf_path", None)
        return rows

    def packet_path(self, packet_id: str) -> Path | None:
        r = self.db.one("SELECT pdf_path FROM packets WHERE id=?", (packet_id,))
        return Path(r["pdf_path"]) if r else None

    # ------------------------------------------------------------ demo
    def reset_demo(self) -> dict:
        """Clear what a run of the demo leaves behind, so it can be played again from the start: alerts, the
        simulated chats, damage reports, packets and thermometer readings. Simulator only: with a real channel
        these are real conversations. The audit log is append-only, so it stays and gets one more entry."""
        if self.channel.name != "sim":
            raise PermissionError("reset is only for the simulator channel")
        from . import parcel_logger
        parcel_logger.ensure_schema(self.db)
        for r in self.db.all("SELECT pdf_path FROM packets"):
            Path(r["pdf_path"]).unlink(missing_ok=True)
        cleared = {}
        for table in ("alerts", "messages", "reports", "packets", "logger_obs", "contacts"):
            cleared[table] = self.db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
            self.db.execute(f"DELETE FROM {table}")
        self.db.execute("DELETE FROM kv_cache WHERE key LIKE 'sitecal:%'")   # per-parcel thermometer calibration
        self.audit.append("officer", "demo.reset", None, cleared)
        return cleared

    def config_info(self) -> dict:
        from . import asr
        return {
            "model_source": model_adapter.effective_source(),
            "model_installed": model_adapter.MODEL_SOURCE == "helada_model",
            "model_error": model_adapter.LAST_ERROR,
            "channel": self.channel.name,
            "tts": tts.available(self.s.tts, self.s.piper_model) or "off",
            "asr": asr.engine_name(self.s.asr, self.s.whisper_model),
            "slot_filler": self.filler.name + (f" ({self.s.llm_model})" if self.s.llm_url else ""),
            "offline": self.s.offline,
            "advisory_signed": A.advisory_signed(),
            "advisory_label": A.advisory()["meta"].get("unsigned_label"),
            "tz": self.s.tz_name,
            "now_local": self.now_local().isoformat(timespec="minutes"),
            "today": self.today().isoformat(),
        }
