"""Make the demo's voice notes ahead of time, for a server with a slow CPU.

    cd app && python scripts/seed_demo_media.py <out_dir>

On one server core piper needs about as long as the voice note lasts, so the six alerts of the replay night would
keep «Enviar avisos» waiting for minutes. This runs the same code once (the alerts of 14 Nov 2025 at 18:30, the
"1 para más" detail of each, and the farmer's sample voice note) and keeps the audio files. The app copies them into
its data dir at start (HELADA_SEED_DIR), where the text-keyed cache finds them. Run it with the same HELADA_TTS and
voice settings the server will use: the cache key includes the voice. Texts that have a recording made ahead
(backend/data/voice, scripts/make_demo_voice.py) use it instead of piper.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

NIGHT, SENT_AT, REPLY_AT = "2025-11-14", "2025-11-14T18:30", "2025-11-14T18:35"


def main(out: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="helada-seed-"))
    os.environ.update({"HELADA_DATA_DIR": str(tmp), "HELADA_SCHEDULER": "0", "HELADA_SEED_DIR": ""})
    from backend import main as web, roster
    from backend.conversation import Inbound, handle_inbound

    svc = web.app.state.svc
    r = svc.send_alerts(NIGHT, scenario="demo", now=SENT_AT, override_window=True)
    sent = [x["parcel_id"] for x in r["results"] if x.get("status") == "sent" and x.get("level") != "watch"]
    for pid in sent:      # "Responda 1 para más": the longer voice note
        phone = roster.get_parcel(svc.db, pid)["phone"]
        handle_inbound(svc, Inbound(phone, "text", "1", received_at=svc.now_local(REPLY_AT), channel="sim"))
    check_sample(svc, web)

    n = 0
    for sub, pattern in (("tts", "*.ogg"), ("samples", "audio_ejemplo_aurelio.ogg*")):
        for f in sorted((svc.s.media_dir / sub).glob(pattern)):
            (out / sub).mkdir(parents=True, exist_ok=True)
            shutil.copy(f, out / sub / f.name)
            n += 1
    print(f"alerts with a voice note: {len(sent)} ({', '.join(sent)}); audio files kept: {n} -> {out}")
    if not n:
        sys.exit("no audio was made: is a TTS backend configured (HELADA_TTS, HELADA_PIPER_MODEL, ffmpeg)?")


def check_sample(svc, web) -> None:
    """Make the farmer's sample voice note and, when the real speech model is installed, make sure it hears it
    right: a sample the bot misreads is a bad sample. Tries the recording made ahead (data/voice), then the sample
    voice, then the alert's voice."""
    from backend import asr, tts
    real = asr.engine_name(svc.s.asr, svc.s.whisper_model) != "mock"
    voices = ([None] if tts.SAMPLE_VOICE.exists() else []) + list(dict.fromkeys([svc.s.sample_piper_model, svc.s.piper_model]))
    for voice in voices:
        if voice is not None:
            svc.s.sample_piper_model = voice
        for f in (svc.s.media_dir / "samples").glob("audio_ejemplo_aurelio.ogg*"):
            f.unlink()
        clip = web._sample_audio(svc, recorded=voice is None)
        if clip is None:
            sys.exit("the sample voice note could not be made")
        if not real:
            print("sample voice note made (speech model not installed: not checked)")
            return
        heard = asr.transcribe(clip, pref=svc.s.asr, whisper_model=svc.s.whisper_model).text or ""
        ok = all(w in heard.lower() for w in ("se quemó la milpa", "hectárea", "anoche", "helada"))
        label = "recorded" if voice is None else (Path(voice).stem or "say")
        print(f"sample voice note [{label}] heard as: {heard!r} -> {'ok' if ok else 'MISHEARD'}")
        if ok:
            return
    sys.exit("the speech model mishears every rendering of the sample voice note")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
