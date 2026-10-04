"""Spanish speech-to-text for inbound voice notes.

voice note (OGG/Opus, webm, m4a...) --ffmpeg--> 16 kHz mono WAV --> ASR.

HELADA_ASR = auto | whisper | mock
  whisper: faster-whisper (`uv sync --extra asr`), model from WHISPER_MODEL (DEFAULT_MODEL "small":
           CTranslate2 int8 on CPU, 486 MB on disk, ~1.3 GB peak RSS, ~1.1 s per 3-5 s voice note
           with 4 threads; WER 2.7% vs 14% for "base" on the synthetic bench, see
           docs/small-ai-bench.md). Downloads from Hugging Face once, then runs offline.
  mock:    returns the transcript stored in a sidecar `<audio>.txt` (used by the demo's
           sample voice notes and by tests); otherwise returns None so the bot asks the
           farmer to type or repeat. Never invents text.
  auto:    whisper if faster-whisper is importable AND the model (WHISPER_MODEL, else DEFAULT_MODEL) is already on
           disk; else mock. So `auto` never downloads inside a request: run `scripts/setup_local_ai.sh` once while
           online. `whisper` forces the real engine and downloads the weights if they are missing.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("helada.asr")

_model_cache: dict[str, object] = {}
_weights_seen: set[str] = set()
_warned: set[str] = set()

DEFAULT_MODEL = "small"
# Domain vocabulary as whisper's initial prompt: biases decoding toward farm words it otherwise
# mishears ("heló" -> "meló", "antier" -> "anterior", "haba" -> "agua"). Plain words, no instructions.
INITIAL_PROMPT = ("Reporte de daños en la milpa. Se me heló el maíz anoche, fue antier. Cayó granizo, "
                  "escarcha, helada. Hectárea y media. Haba, frijol, avena, papa. Parcela en Toluca.")


@dataclass
class Transcript:
    text: str | None
    engine: str
    language: str = "es"
    seconds: float | None = None


def to_wav16k(src: Path, dst: Path) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(dst)], check=True, capture_output=True, timeout=120)
    return dst


def weights_cached(name: str) -> bool:
    """True if the model is already on disk, so loading it needs no network."""
    if name in _weights_seen:
        return True
    try:
        from faster_whisper.utils import download_model
        download_model(name, local_files_only=True)
    except Exception:
        return False
    _weights_seen.add(name)
    return True


def engine_name(pref: str, whisper_model: str) -> str:
    if pref == "mock":
        return "mock"
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return "mock"
    if pref == "whisper":
        return "whisper"
    # auto: the real engine when it is installed (`uv sync --extra asr`) and its weights are on disk, so the first
    # voice note never waits for a 486 MB download on an offline box; WHISPER_MODEL only picks the size
    name = whisper_model or DEFAULT_MODEL
    if weights_cached(name):
        return "whisper"
    if name not in _warned:
        _warned.add(name)
        log.warning("faster-whisper is installed but the %r weights are not on disk: using stored transcripts. "
                    "Run scripts/setup_local_ai.sh once while online.", name)
    return "mock"


def transcribe(audio: Path, *, pref: str = "auto", whisper_model: str = "") -> Transcript:
    audio = Path(audio)
    eng = engine_name(pref, whisper_model)
    if eng == "mock":
        side = audio.with_suffix(audio.suffix + ".txt")
        if not side.exists():
            side = audio.with_suffix(".txt")
        return Transcript(side.read_text(encoding="utf-8").strip() if side.exists() else None, "mock")
    from faster_whisper import WhisperModel
    name = whisper_model or DEFAULT_MODEL
    m = _model_cache.get(name)
    if m is None:
        m = WhisperModel(name, device="cpu", compute_type="int8")
        _model_cache[name] = m
    wav = audio.with_suffix(".16k.wav")
    to_wav16k(audio, wav)
    # feed PCM directly (avoids faster-whisper's PyAV decoder, which breaks on some av versions)
    import wave
    import numpy as np
    with wave.open(str(wav), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    wav.unlink(missing_ok=True)
    segs, info = m.transcribe(pcm, language="es", beam_size=1, vad_filter=True,
                              initial_prompt=INITIAL_PROMPT)
    text = " ".join(s.text.strip() for s in segs).strip()
    if not text:   # nothing heard: a built-in sample still carries its transcript next to the file
        side = audio.with_suffix(audio.suffix + ".txt")
        if side.exists():
            return Transcript(side.read_text(encoding="utf-8").strip() or None, "sidecar")
    return Transcript(text or None, f"faster-whisper:{name}", "es", getattr(info, "duration", None))
