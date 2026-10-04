"""Channel adapters: sim (default, in-browser phone), twilio_whatsapp (sandbox), sms (Twilio SMS fallback).

The service layer always stores every outbound message in SQLite (so the dashboard
and the phone simulator can show it); adapters only handle external delivery.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import Settings

log = logging.getLogger("helada.channels")


@dataclass
class OutMsg:
    phone: str                      # E.164, e.g. +527221234567
    text: str | None = None
    audio_path: Path | None = None  # local file (voice note)
    media_path: Path | None = None  # local file (image/pdf)
    sms_text: str | None = None     # short GSM-7 version for SMS


class SimChannel:
    name = "sim"

    def __init__(self, settings: Settings):
        self.s = settings

    def send(self, m: OutMsg) -> dict:
        return {"status": "delivered", "provider_id": None, "channel": self.name}


class TwilioBase:
    api = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

    def __init__(self, settings: Settings):
        self.s = settings
        if not (settings.twilio_sid and settings.twilio_token):
            raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set")

    def _post(self, data: dict) -> dict:
        r = httpx.post(self.api.format(sid=self.s.twilio_sid), data=data,
                       auth=(self.s.twilio_sid, self.s.twilio_token), timeout=20)
        if r.status_code >= 300:
            log.warning("Twilio error %s: %s", r.status_code, r.text[:300])
            return {"status": "failed", "error": r.text[:300]}
        js = r.json()
        return {"status": js.get("status", "queued"), "provider_id": js.get("sid")}

    def public_media_url(self, path: Path | None) -> str | None:
        if not path or not self.s.public_url:
            return None
        try:
            rel = Path(path).resolve().relative_to(self.s.data_dir.resolve())
        except ValueError:
            return None
        return f"{self.s.public_url}/files/{rel.as_posix()}"


class TwilioWhatsAppChannel(TwilioBase):
    """[V] untested against a live account. Sandbox: recipients must first send 'join <code>'.
    Business-initiated messages outside the 24 h session need an approved template in production."""
    name = "twilio_whatsapp"

    def send(self, m: OutMsg) -> dict:
        to = m.phone if m.phone.startswith("whatsapp:") else f"whatsapp:{m.phone}"
        res = {"channel": self.name, "parts": []}
        if m.text:
            res["parts"].append(self._post({"From": self.s.twilio_whatsapp_from, "To": to, "Body": m.text}))
        for p in (m.audio_path, m.media_path):
            url = self.public_media_url(p)
            if url:
                res["parts"].append(self._post({"From": self.s.twilio_whatsapp_from, "To": to, "MediaUrl": url}))
            elif p:
                res["parts"].append({"status": "skipped", "error": "HELADA_PUBLIC_URL not set; media not sent"})
        ok = [x for x in res["parts"] if x.get("status") not in ("failed", "skipped")]
        res["status"] = "sent" if ok else "failed"
        res["provider_id"] = ok[0].get("provider_id") if ok else None
        return res


class SmsChannel(TwilioBase):
    """[V] untested. Text only; uses the short GSM-7 version (one 160-char segment)."""
    name = "sms"

    def send(self, m: OutMsg) -> dict:
        if not self.s.twilio_sms_from:
            return {"status": "failed", "error": "TWILIO_SMS_FROM not set", "channel": self.name}
        body = m.sms_text or (m.text or "")[:300]
        r = self._post({"From": self.s.twilio_sms_from, "To": m.phone.replace("whatsapp:", ""), "Body": body})
        return {**r, "channel": self.name}


def make_channel(settings: Settings, name: str | None = None):
    name = name or settings.channel
    if name == "twilio_whatsapp":
        return TwilioWhatsAppChannel(settings)
    if name == "sms":
        return SmsChannel(settings)
    return SimChannel(settings)


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """X-Twilio-Signature = base64(HMAC-SHA1(token, url + concat(sorted(k+v))))."""
    s = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), s.encode("utf-8"), hashlib.sha1).digest()).decode()


def validate_twilio(auth_token: str, url: str, params: dict[str, str], signature: str | None) -> bool:
    if not signature or not auth_token:
        return False
    return hmac.compare_digest(twilio_signature(auth_token, url, params), signature)
