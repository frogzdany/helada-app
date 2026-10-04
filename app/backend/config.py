"""Runtime configuration from environment variables (all optional)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = APP_DIR / "backend"
STATIC_DIR = APP_DIR / "static"


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("HELADA_DATA_DIR", APP_DIR / "var")))
    channel: str = field(default_factory=lambda: os.environ.get("HELADA_CHANNEL", "sim"))
    tz_name: str = field(default_factory=lambda: os.environ.get("HELADA_TZ", "America/Mexico_City"))
    offline: bool = field(default_factory=lambda: _b("HELADA_OFFLINE", False))
    # TTS: auto | say | piper | off
    tts: str = field(default_factory=lambda: os.environ.get("HELADA_TTS", "auto"))
    tts_voice: str = field(default_factory=lambda: os.environ.get("HELADA_TTS_VOICE", "Paulina"))
    piper_model: str = field(default_factory=lambda: os.environ.get("HELADA_PIPER_MODEL", ""))
    # a second piper voice for the built-in sample voice note (the farmer), where macOS `say` is not available
    sample_piper_model: str = field(default_factory=lambda: os.environ.get("HELADA_SAMPLE_PIPER_MODEL", ""))
    # seconds of voice synthesis one «Enviar avisos» may spend; past it the remaining alerts go as text only (0 = no limit)
    tts_budget_s: float = field(default_factory=lambda: float(os.environ.get("HELADA_TTS_BUDGET_S", "0") or 0))
    # voice notes made ahead of time (scripts/seed_demo_media.py), copied into the data dir at start
    seed_dir: str = field(default_factory=lambda: os.environ.get("HELADA_SEED_DIR", ""))
    # ASR: auto | whisper | mock
    asr: str = field(default_factory=lambda: os.environ.get("HELADA_ASR", "auto"))
    whisper_model: str = field(default_factory=lambda: os.environ.get("WHISPER_MODEL", ""))
    # OpenAI-compatible base URL, e.g. http://localhost:11434/v1 (Ollama) or http://localhost:8080/v1 (llama.cpp)
    llm_url: str = field(default_factory=lambda: os.environ.get("HELADA_LLM", ""))
    llm_model: str = field(default_factory=lambda: os.environ.get("HELADA_LLM_MODEL", "qwen3:1.7b"))
    # Twilio
    twilio_sid: str = field(default_factory=lambda: os.environ.get("TWILIO_ACCOUNT_SID", ""))
    twilio_token: str = field(default_factory=lambda: os.environ.get("TWILIO_AUTH_TOKEN", ""))
    twilio_whatsapp_from: str = field(default_factory=lambda: os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"))
    twilio_sms_from: str = field(default_factory=lambda: os.environ.get("TWILIO_SMS_FROM", ""))
    twilio_validate: bool = field(default_factory=lambda: _b("TWILIO_VALIDATE_SIGNATURE", True))
    public_url: str = field(default_factory=lambda: os.environ.get("HELADA_PUBLIC_URL", "").rstrip("/"))
    # background sender for queued alerts (evening window)
    scheduler: bool = field(default_factory=lambda: _b("HELADA_SCHEDULER", True))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "helada.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def packets_dir(self) -> Path:
        return self.data_dir / "packets"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.media_dir, self.packets_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
