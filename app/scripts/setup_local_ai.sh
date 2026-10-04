#!/usr/bin/env bash
# Set up the on-device "small AI" for Helada: Spanish ASR (faster-whisper) + a 1-2B slot-filling LLM (Ollama).
# Nothing here needs a GPU or a cloud account. Run from app/.
#
#   bash scripts/setup_local_ai.sh            # ASR model + qwen3:1.7b slot model
#   ASR_MODEL=base LLM_BASE=gemma3:1b bash scripts/setup_local_ai.sh
#
# Prereqs you install yourself (this script does not install system software):
#   * ffmpeg            (brew install ffmpeg | apt install ffmpeg)
#   * uv                (https://docs.astral.sh/uv/)
#   * Ollama (optional) (https://ollama.com/download; Linux: curl -fsSL https://ollama.com/install.sh | sh)
set -euo pipefail
ASR_MODEL="${ASR_MODEL:-small}"
LLM_BASE="${LLM_BASE:-qwen3:1.7b}"
LLM_TAG="${LLM_TAG:-helada-slots}"

command -v ffmpeg >/dev/null || { echo "ffmpeg missing"; exit 1; }
uv sync --extra asr
# Download the CTranslate2 int8 whisper weights once (Hugging Face cache), then it runs offline.
uv run --extra asr python -c "from faster_whisper import WhisperModel; WhisperModel('$ASR_MODEL', device='cpu', compute_type='int8'); print('whisper $ASR_MODEL ready')"

if command -v ollama >/dev/null; then
  ollama pull "$LLM_BASE"
  # Small context (2k) + CPU-only keeps RAM at ~1.5 GB instead of ~6 GB with Ollama's 40k default ctx.
  MF="$(mktemp)"
  sed "s|^FROM .*|FROM $LLM_BASE|" scripts/ollama/Modelfile.slots > "$MF"
  ollama create "$LLM_TAG" -f "$MF"
  echo "LLM ready: HELADA_LLM=http://localhost:11434/v1 HELADA_LLM_MODEL=$LLM_TAG"
else
  echo "Ollama not installed: slot filling stays on the deterministic regex filler (fully supported)."
fi
echo "Run: WHISPER_MODEL=$ASR_MODEL HELADA_LLM=http://localhost:11434/v1 HELADA_LLM_MODEL=$LLM_TAG uv run --extra asr uvicorn backend.main:app"
