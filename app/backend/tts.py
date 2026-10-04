"""Offline text-to-speech for alert voice notes (optional, cached).

Backends (HELADA_TTS): piper (needs `piper` CLI + HELADA_PIPER_MODEL .onnx, e.g.
es_MX-claude-high) | say (macOS dev fallback, voice Paulina es_MX) | auto | off.
Output: OGG/Opus mono 16 kHz via ffmpeg (what WhatsApp plays as a voice note).
Cache key = sha256(backend|voice|text), so re-sending the same alert is free.

Recordings (data/voice/, made by scripts/make_demo_voice.py): when a text has a voice note recorded ahead, that
file is used instead of a backend, also where no backend is installed. Only the demo's replay night has them.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("helada.tts")

VOICE_DIR = Path(__file__).parent / "data" / "voice"
SAMPLE_VOICE = VOICE_DIR / "sample_farmer.ogg"     # the farmer's sample voice note of the demo


def prerecorded(text: str) -> Path | None:
    """The voice note recorded ahead for exactly this text, if there is one."""
    f = VOICE_DIR / f"{hashlib.sha256(text.encode()).hexdigest()[:24]}.ogg"
    return f if f.exists() else None


def _backend(pref: str, piper_model: str) -> str | None:
    if pref == "off" or not shutil.which("ffmpeg"):
        return None
    if pref in ("piper", "auto") and shutil.which("piper") and piper_model and Path(piper_model).exists():
        return "piper"
    if pref in ("say", "auto") and shutil.which("say"):
        return "say"
    return None


def available(pref: str = "auto", piper_model: str = "") -> str | None:
    return _backend(pref, piper_model)


def synthesize(text: str, out_dir: Path, *, pref: str = "auto", voice: str = "Paulina",
               piper_model: str = "") -> Path | None:
    if pref == "off":
        return None
    pre = prerecorded(text)
    if pre is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"tts_pre_{pre.stem}.ogg"
        if not out.exists():
            shutil.copy(pre, out)
        return out
    be = _backend(pref, piper_model)
    if be is None:
        return None
    key = hashlib.sha256(f"{be}|{voice}|{piper_model}|{text}".encode()).hexdigest()[:24]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"tts_{key}.ogg"
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / ("a.aiff" if be == "say" else "a.wav")
            if be == "say":
                subprocess.run(["say", "-v", voice, "-r", "165", "-o", str(raw), text],
                               check=True, capture_output=True, timeout=120)
            else:
                subprocess.run(["piper", "--model", piper_model, "--output_file", str(raw)],
                               input=text.encode("utf-8"), check=True, capture_output=True, timeout=120)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-ac", "1", "-ar", "16000",
                            "-c:a", "libopus", "-b:a", "24k", "-application", "voip", str(out)],
                           check=True, capture_output=True, timeout=120)
        return out
    except Exception as e:
        log.warning("TTS failed (%s): %s", be, e)
        return None
