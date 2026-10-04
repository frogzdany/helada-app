# Helada dashboard and backend as one container image.
# For any host that runs ONE instance, or a laptop (Dockerfile.aws is the same image for AWS Lambda):
#   docker build -t helada . && docker run --rm -p 8080:80 helada
#
# What is inside, so the public demo behaves like the laptop one:
#   - the app (FastAPI, static frontend) and helada_model with its artifacts
#   - ffmpeg (static build) for voice notes in and out
#   - piper with two Mexican Spanish voices: the alert's voice note, and the built-in sample of the farmer
#   - faster-whisper "small" with its weights, so voice notes are transcribed in the container, offline
# State (SQLite, media, packets) lives in /tmp: it lasts while the instance is warm and starts clean after that.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy HF_HOME=/srv/hf

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=mwader/static-ffmpeg:7.1 /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ghcr.io/astral-sh/uv:0.8.3 /uv /usr/local/bin/uv

WORKDIR /srv/helada
# dependencies first (a cached layer): the app's lock file, and the model package it depends on by path
COPY model/pyproject.toml model/README.md model/
COPY model/src model/src
COPY app/pyproject.toml app/uv.lock app/
RUN cd app && uv sync --frozen --no-dev --no-cache --extra asr --extra tts

# piper voices (rhasspy/piper-voices): claude/high is Apache-2.0, ald/medium is Unlicense
ARG VOICES=https://huggingface.co/rhasspy/piper-voices/resolve/c10ece1aade47bb51c153c893d14e5bf8e5b7117/es/es_MX
ADD --chmod=644 --checksum=sha256:3ef40a71ea63852cd8ab7e6fa7d2ecdcfa67a0b47c9c48e3f10e02ee02083ea0 \
    ${VOICES}/claude/high/es_MX-claude-high.onnx /srv/voices/
ADD --chmod=644 ${VOICES}/claude/high/es_MX-claude-high.onnx.json /srv/voices/
ADD --chmod=644 --checksum=sha256:019b3803293c93e34a206dd2e53a3889209a514e786fd7144f7b70196c579b63 \
    ${VOICES}/ald/medium/es_MX-ald-medium.onnx /srv/voices/
ADD --chmod=644 ${VOICES}/ald/medium/es_MX-ald-medium.onnx.json /srv/voices/

# speech-to-text weights (486 MB), downloaded once here so no request ever waits for them
RUN app/.venv/bin/python -c "from faster_whisper.utils import download_model; download_model('small')"

ENV PATH=/srv/helada/app/.venv/bin:$PATH HOME=/tmp HF_HUB_OFFLINE=1 \
    HELADA_DATA_DIR=/tmp/helada HELADA_CHANNEL=sim HELADA_SCHEDULER=0 \
    HELADA_TTS=piper HELADA_PIPER_MODEL=/srv/voices/es_MX-claude-high.onnx \
    HELADA_SAMPLE_PIPER_MODEL=/srv/voices/es_MX-ald-medium.onnx HELADA_TTS_BUDGET_S=40 \
    HELADA_ASR=auto WHISPER_MODEL=small

COPY app/backend app/backend
COPY app/scripts/seed_demo_media.py app/scripts/
# the replay night's voice notes, made once here: on one server core each takes about as long as it lasts
# (static/ comes after this step so a page edit does not redo it; the app only needs the folder to exist)
RUN mkdir -p app/static && cd app && python scripts/seed_demo_media.py /srv/seed
ENV HELADA_SEED_DIR=/srv/seed
COPY app/static app/static

WORKDIR /srv/helada/app
EXPOSE 80
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-80} --proxy-headers --forwarded-allow-ips='*'"]
