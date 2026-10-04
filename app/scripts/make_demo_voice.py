"""Record the replay night's voice notes ahead of time with a studio voice (ElevenLabs), for the demo.

    cd app && ELEVENLABS_API_KEY=... uv run python scripts/make_demo_voice.py            # make what is missing
    cd app && uv run python scripts/make_demo_voice.py --list                            # texts and sizes, no calls

The app's own voice is piper, on the box, with no network. Its es_MX voices sound synthetic, and the demo is where
people listen. So the voice notes of the replay night (the alerts of 14 Nov 2025 at 18:30, the "1 para más" detail
of each, the sowing answer for P01 and the farmer's sample voice note) are recorded once here and kept in
backend/data/voice/. `tts.synthesize` plays a recording when the text is exactly the recorded one, and falls back
to piper for every other text: any other night, a changed alert wording. Nothing calls ElevenLabs at run time.

A file is named by the hash of its text, so re-running only records texts that have none. After changing the alert
wording, the advisory table or the roster, run it again (tests/test_alerts.py fails until the night is covered).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

NIGHT, SENT_AT, REPLY_AT = "2025-11-14", "2025-11-14T18:30", "2025-11-14T18:35"
MODEL = "eleven_multilingual_v2"
# ElevenLabs voice library, Mexican Spanish. Alerts: a warm, clear, mature voice. Sample: the farmer (Aurelio).
ALERT_VOICE = {"id": "hHjbwzYZW17oh0p05AKv", "name": "Gabriela - Warm, Rich and Balanced"}
FARMER_VOICE = {"id": "8i7N5S2zKx4WFzUTUiCY", "name": "Mem - Enveloping, Warm and Serene"}
SETTINGS = {"stability": 0.55, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True}


def replay_texts() -> list[str]:
    """Every text the replay night turns into a voice note, in the order the demo plays them."""
    from backend import main as web, roster, tts
    from backend.conversation import Inbound, handle_inbound

    texts: list[str] = []

    def record(text, *args, **kw):
        if text not in texts:
            texts.append(text)
        return None

    tts.synthesize = record
    svc = web.app.state.svc
    r = svc.send_alerts(NIGHT, scenario="demo", now=SENT_AT, override_window=True)
    sent = [x["parcel_id"] for x in r["results"] if x.get("status") == "sent" and x.get("level") != "watch"]
    if not sent:
        sys.exit("the replay night sent no alert: is helada_model installed?")
    for pid in sent:
        phone = roster.get_parcel(svc.db, pid)["phone"]
        handle_inbound(svc, Inbound(phone, "text", "1", received_at=svc.now_local(REPLY_AT), channel="sim"))
    phone = roster.get_parcel(svc.db, "P01")["phone"]
    handle_inbound(svc, Inbound(phone, "text", "¿cuándo siembro?", received_at=svc.now_local(REPLY_AT), channel="sim"))
    return texts


def speak(text: str, voice: dict, out: Path, key: str) -> None:
    """One ElevenLabs call, then the same encoding tts.synthesize uses (OGG/Opus mono 16 kHz, a WhatsApp voice note)."""
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice['id']}?output_format=mp3_44100_128",
        data=json.dumps({"text": text, "model_id": MODEL, "voice_settings": SETTINGS}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=120) as r, tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "a.mp3"
        raw.write_bytes(r.read())
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-ac", "1", "-ar", "16000",
                        "-c:a", "libopus", "-b:a", "24k", "-application", "voip", str(out)],
                       check=True, capture_output=True, timeout=120)


def main() -> None:
    # a scratch data dir, set before the app is imported: the replay must start with no alert sent yet
    os.environ.update({"HELADA_DATA_DIR": tempfile.mkdtemp(prefix="helada-voice-"), "HELADA_SCHEDULER": "0",
                       "HELADA_SEED_DIR": "", "HELADA_CHANNEL": "sim", "HELADA_OFFLINE": "1"})
    from backend import tts
    from backend.main import SAMPLE_AUDIO_TEXT

    jobs = [(t, ALERT_VOICE, tts.VOICE_DIR / f"{hashlib.sha256(t.encode()).hexdigest()[:24]}.ogg")
            for t in replay_texts()]
    jobs.append((SAMPLE_AUDIO_TEXT, FARMER_VOICE, tts.SAMPLE_VOICE))
    todo = [j for j in jobs if not j[2].exists()]
    print(f"{len(jobs)} voice notes, {sum(len(t) for t, _, _ in jobs)} characters; "
          f"to record: {len(todo)}, {sum(len(t) for t, _, _ in todo)} characters")
    if "--list" in sys.argv:
        for t, v, f in jobs:
            print(f"\n[{f.name}] {v['name']} · {len(t)} chars{'' if f.exists() else ' · MISSING'}\n{t}")
        return
    key = os.environ.get("ELEVENLABS_API_KEY")
    if todo and not key:
        sys.exit("set ELEVENLABS_API_KEY to record the missing ones")
    key = key or ""
    tts.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    for t, v, f in todo:
        speak(t, v, f, key)
        print(f"recorded {f.name} ({f.stat().st_size // 1024} KB) · {v['name']}")
    keep = {f.name for _, _, f in jobs}
    stale = sorted(f.name for f in tts.VOICE_DIR.glob("*.ogg") if f.name not in keep)
    manifest = {"made_with": f"ElevenLabs {MODEL}", "note": "recorded ahead for the demo; the app's own voice is piper",
                "files": {f.name: {"voice": v["name"], "text": t} for t, v, f in jobs}}
    (tts.VOICE_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if stale:
        print("no longer used (their text changed), safe to delete:", ", ".join(stale))


if __name__ == "__main__":
    main()
