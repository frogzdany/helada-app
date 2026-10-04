"""Generate the SYNTHETIC Spanish voice-note test clips used by bench_small_ai.py.

macOS `say` (Spanish voices) -> AIFF -> ffmpeg -> OGG/Opus 16 kHz mono, 24 kbit/s
(the format WhatsApp voice notes arrive in). Clips whose manifest entry has
`noise` get ffmpeg `anoisesrc` (pink/brown) mixed in at roughly 10 dB SNR.

These are TTS voices, not real farmers: they measure the pipeline and relative model
size/latency, not real-world field accuracy (accents, wind, cheap mics).

    cd app && python scripts/make_test_clips.py          # needs macOS `say` + ffmpeg
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIPS = HERE / "bench_clips"
MANIFEST = CLIPS / "manifest.json"


def available_voices() -> set[str]:
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout
    names = set()
    for line in out.splitlines():
        # "Eddy (Español (México)) es_MX    # ..." -> name is everything before the locale column
        head = line.split("#")[0].rstrip()
        parts = head.rsplit(None, 1)
        if len(parts) == 2:
            names.add(parts[0].strip())
    return names


def make_clip(text: str, voice: str, noise: str | None, dst: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        aiff = Path(td) / "v.aiff"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        enc = ["-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k", "-application", "voip"]
        if noise:
            # speech (normalised) + continuous noise, trimmed to the speech length
            flt = (f"[0:a]aresample=16000,aformat=channel_layouts=mono,volume=1.0[s];"
                   f"anoisesrc=color={noise}:amplitude=0.06:sample_rate=16000[n];"
                   f"[s][n]amix=inputs=2:duration=first:normalize=0[out]")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-filter_complex", flt,
                   "-map", "[out]", *enc, str(dst)]
        else:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), *enc, str(dst)]
        subprocess.run(cmd, check=True)


def main() -> int:
    if not (shutil.which("say") and shutil.which("ffmpeg")):
        print("needs macOS `say` and ffmpeg", file=sys.stderr)
        return 1
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    voices = available_voices()
    for c in man["clips"]:
        voice = c["voice"] if c["voice"] in voices else "Paulina"
        if voice != c["voice"]:
            print(f"{c['id']}: voice {c['voice']!r} not installed, using Paulina")
        dst = CLIPS / f"{c['id']}.ogg"
        make_clip(c["text"], voice, c.get("noise"), dst)
        print(f"{dst.name}  {dst.stat().st_size / 1024:5.1f} KB  {voice}{' +' + c['noise'] + ' noise' if c.get('noise') else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
