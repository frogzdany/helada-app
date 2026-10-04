# Helada app: channel, backend, dashboard, phone page

This app sends parcel-level frost warnings as WhatsApp voice notes. When a farmer reports a loss by voice note and photos, it builds a **loss-evidence packet** (PDF) for Edomex's PASACME crop-loss program.

- It is local-first: FastAPI, SQLite and a static frontend, with no build step, on one laptop or a Pi-class box.
- The only network calls are **optional**: Open-Meteo for forecasts, OSM tiles for the map, and Twilio for real WhatsApp/SMS.

> All owner names, phones and areas in the demo roster are **FICTIONAL**. The coordinates are real points in Almoloya de Juárez, Temoaya, Zinacantepec, Ixtlahuaca, Atlacomulco and San Felipe del Progreso (land use not checked).
> - **10 parcels are ~0.9 km from a real SMN station** (DEM elevation within 50 m), so `helada_model` treats them as **station-anchored**, the validated regime (`near_station` in `roster.json`).
> - **P05 and P10 are > 6 km from any station: terrain-transfer.** They show the wide band and the "install a logger" message.

## Run locally

```bash
cd app
uv sync
uv run uvicorn backend.main:app --port 8000
# open http://localhost:8000
uv run pytest -q          # about 2,200 tests, fully offline (a few run the real helada_model)
```

### The demo that runs by itself

Press **▶ Demo** in the header (or open `/#demo`). It plays the replay night of 14 Nov 2025 through every screen, 24
steps, with a caption for each: the map and the plot card, a miss and a false alarm, sending the alerts, the voice
note, the farmer's reply, photos and location, the packet, the audit log, the sowing calendar, the thermometer, the
evidence, and tonight's live forecast. It uses the same functions as the buttons, so what you see is the real screen.

- Player bar: previous, pause/play, next, speed (1×, 1.5×, 2×), captions in Spanish or English, close. Keys: space,
  ← →, Esc. Click a segment of the progress line to jump to that step; the steps before it that send something are
  run first, so the story is never half told.
- Each start calls `POST /api/demo/reset`, which clears alerts, the simulated chats, reports, packets and thermometer
  readings so the run is the same every time. It is refused (409) unless the channel is the simulator. The audit log
  is append-only: it is kept, and the reset is one more entry in it.
- The last step links to the phone page's own demo (`/static/movil/index.html#tour`).

### Demo script by hand (no Twilio needed)

> **Dashboard language.** The dashboard opens in English. The names in the steps below are the Spanish ones: press **Español** in the header to see them, or read them as: Pronóstico = Forecast · Enviar avisos = Send alerts · Reiniciar demostración = Reset demo · Mapa y pronóstico = Map and forecast · Paquetes = Packets · Bitácora = Audit log · Registrador en la parcela = Thermometer on the plot · Evidencia del modelo = Model evidence · Detalles técnicos = Technical details · Su parcela = On the plot. The phone chat, the SMS and the PDF stay in Spanish.

1. In "Pronóstico", choose **Noche del 14 al 15 de noviembre de 2025**. This replays a real night from the model's held-out season: the archived Open-Meteo `ecmwf_ifs025` day-1 forecast (the model's input) and the SMN Tmin observed next to each station-anchored parcel. The date is fixed to 2025‑11‑14, and **Hora del sistema** (System time; it shows with «Detalles técnicos») is set to 2025‑11‑14 18:30 if empty.
   - The replay runs `helada_model` with `variant="holdout_2025_26"`, which is trained without the 2025‑26 season. It is labeled "Réplica: modelo entrenado sin la temporada 2025-26" in the UI, alerts and packet, so the demo never shows an in-sample result. Live nights use the shipped model.
   - Headline: P01, 0.9 km from SMN 15282 Tres Barrancas. Forecast **+9.2 °C**, observed **−3.0 °C**; the model gives **+0.5 °C, P = 54%**, so an alert.
   - Result on the 12 parcels: **6 ALERT** (P01, P02, P03, P07, P08, P09), **1 WATCH** (P10, terrain-transfer, band down to −0.1 °C), 5 nothing.
   - Counterexamples on the same night: P06 (Toluca Obs.) was **missed** (forecast +8.9, observed −1.1, held-out P = 19%). P09 (Santa María del Llano) was a **false alarm** (held-out P = 65%, observed +3.0).
   - Fixture: `backend/data/demo_replay.json`, built by `scripts/build_demo_replay.py` (needs `../data`, `../model/research/out` and network for the two transfer parcels).
2. Click a parcel. You get the side-by-side **Pronóstico regional vs. Su parcela**, P(helada), the drivers and a climatology line.
   **Calendario de siembra** (tab) shows that parcel's frost dates and, for the sowing day you pick, whether each maize
   cycle matures before the first autumn frost.
3. Press **Enviar avisos**. The phone simulator (right) receives the Spanish text and the voice note (`say -v Paulina`).
4. After «Enviar avisos» the system time moves by itself to the next morning (2025‑11‑15 08:00), so "anoche" means the replayed night. On the phone, reply in this order:
   - **🎤 Audio de ejemplo**: "Sí, se quemó la milpa, como una hectárea. Fue anoche…"
   - **📷 Foto de ejemplo** ×4 (synthetic, EXIF time and GPS near the parcel)
   - **📍 Ubicación** (optional)
   - Answer "sí" or "no" to the pre-registro question.

   Instead of the samples you can type, record with the mic, or attach real audio or photo files.
5. The PDF packet arrives in the chat and in **Paquetes**. **Bitácora** shows the hash chain ("Cadena íntegra").
6. **Registrador en la parcela** (tab). This is how an off-station parcel gains accuracy from its own readings; the numbers are measured in `../model/README.md`, "Logger learning curve".
   - **Ejemplo con datos reales (Jocotitlán)** uses SMN 15390, a station the model never saw, as the logger. Its 44 nights before 14 Nov 2025 calibrate the site.
     - The band for the replay night goes from +2.0 [−2.1, +6.9] °C, P 41% (alert) to +3.1 [−0.5, +5.4] °C, P 17%. The station measured +2.0 °C.
     - On the rest of that season the error went 1.94 → 1.34 °C. Fixture: `backend/data/demo_logger.json`, built by `scripts/build_demo_logger.py` (needs `../data` and network).
   - **Uploading for a parcel.** Pick P05 or P10, then paste `fecha,tmin_c` lines or upload a CSV. The date is the morning of the reading by default. You get "noches registradas: k; precisión esperada: ±X °C" and the before/after band for the night chosen in «Mapa y pronóstico».
     - Archived day-1 forecasts for the logged nights come from the Open-Meteo Previous Runs API and are cached. Offline, nights without a cached forecast are listed as skipped.
     - The forecast table then shows the parcel as «Registrador (k noches)».
   - **Farmer on the phone.** On P05's phone (+527220000005), type «anoche marcó -2» and then «sí». The reading is stored after the confirmation, the parcel is recalibrated, and the reply says how many nights it has and the expected error.
     - Parsing is deterministic (`backend/parcel_logger.py`); a damage report is never taken as a reading.
     - Each step is in **Bitácora**: `logger.reading.proposed`, `logger.reading.confirmed`, `logger.calibrated`, `logger.readings.added`.
   - Logger parcels keep the «vigilar» safety net until 150 nights: in the backtest, frost detection from a partial first season was not reliable.

## The dashboard as a container image

`../Dockerfile` builds the dashboard and the backend as one image (`../Dockerfile.aws` is the same image for AWS Lambda, see `../infra/README.md`):

```bash
docker build -t helada . && docker run --rm -p 8080:80 helada      # from the repository root
```

What the image carries, so a server does what the laptop does:

- **Voice out**: piper (`uv sync --extra tts`, GPL-3.0, run as a separate program) with two Mexican Spanish voices from
  `rhasspy/piper-voices`: `es_MX-claude-high` (Apache-2.0) for the alerts and `es_MX-ald-medium` (Unlicense) for the
  farmer's sample voice note. `ffmpeg` is a static build.
- **Voice in**: faster-whisper `small` with its weights inside the image, so voice notes are transcribed in the
  container with no outside service (about 720 MB of memory in use once loaded).
- **The replay night's voice notes are recordings.** The six alerts of 14 Nov 2025, the sowing answer for P01 and the
  farmer's sample voice note were recorded ahead with two ElevenLabs library voices (`scripts/make_demo_voice.py`,
  files in `backend/data/voice/`, listed in its `manifest.json`). `tts.synthesize` plays a recording when the text is
  exactly the recorded one, also on a machine with no speech engine, and uses piper for every other text. Nothing
  calls ElevenLabs at run time. After changing the alert wording, the roster or the advisory table, run the script
  again (a test fails until the night is covered).
- **Voice notes are put in place when the image is built** (`scripts/seed_demo_media.py`, copied in at
  start through `HELADA_SEED_DIR`). On one server core piper needs about as long as the voice note lasts, so six
  alerts would keep «Enviar avisos» waiting for minutes. The same step makes the farmer's sample voice note without
  generator noise and fails the build if the speech model mishears it.
- `HELADA_TTS_BUDGET_S=40`: on a night that was not made ahead (the live one), one «Enviar avisos» spends at most
  about that long on voice; alerts past it go out as text only.

**It needs a host that runs ONE instance.** SQLite, media and packets live in `/tmp` of the running container: they
last while it runs and start clean after a restart, which is fine for a demo that resets itself.

- **A host that starts several instances does not fit.** Each instance would have its own empty `/tmp`, so the
  alerts would land on one and the chat be read from another. On AWS the state is kept outside the container
  (`backend/cloud.py`, DynamoDB and S3) and the function is capped at one instance: `../docs/aws-architecture.md`.
- Anyone with the link can press the buttons, including the demo reset: there is no login. Uploads are capped at 12 MB.

## Speech and the bot's confirmation

- **Speech-to-text is real once it is set up**: run `bash scripts/setup_local_ai.sh` once while online. It installs
  faster-whisper (`uv sync --extra asr`) and downloads the `small` model (486 MB); after that it runs offline and
  `HELADA_ASR=auto` transcribes recorded voice notes. `auto` never downloads inside a request: if the extra or the
  weights are missing, the built-in sample voice notes still work from their stored transcripts and a recorded note
  gets "no le entendí". `HELADA_ASR=whisper` forces the real engine and downloads the weights if needed.
- **The bot says back what it understood** before asking for photos: «Entendí: maíz de temporal, helada, 1 hectárea,
  el viernes 14 de noviembre. Si algo no está bien, dígamelo y lo corrijo.» A correction is just another message; the
  line is repeated with the change.
- **Evidencia del modelo** opens with one real night per station: regional forecast, Helada's estimate with its
  range, and what was measured, on one temperature axis.

## Phone page (the model on the farmer's device)

`static/movil/` is a small installable web page that runs the frost model **on the phone, with no connection**:
open `/movil` (or `/static/movil/index.html#demo` for the replay night).

- `helada-model.js` is a port of the inference path of `helada_model` (features, both regimes, calibration, the
  logger correction) and of the alert rule. It reads the same LightGBM text artifacts as the server
  (`data/models/*.txt.gz`, 372 KB in total). `tests/test_movil_parity.py` replays 204 cases from the Python model
  through it (worst difference 0.005 °C, 0.0005 in probability).
- `data/pack.json` is the "site pack" written by `scripts/build_movil_pack.py`: per-parcel terrain features and
  station anchoring (they need the DEM, so they are computed once, when the parcel is registered), calibration,
  the advisory table and the fail-safe thresholds. `pack-demo.json` is the held-out variant plus the archived
  forecasts of the replay night. Re-run the script after retraining or editing the roster or the advisory table.
- `sw.js` caches the page, the packs and the model files (about 1 MB), so it opens with no signal. Service workers
  need HTTPS or `localhost`: on a phone, use the deployed link, not the laptop's LAN address.
- With signal it downloads the forecast for the next 8 nights (Open-Meteo) and keeps it; with no signal it uses
  the saved one and says how old it is. Thermometer readings and damage reports are saved on the phone and sent
  when the signal is back (`/api/logger/{id}/readings`, `/api/sim/inbound`).
- **Fail-safe.** `judge()` returns `no_seguro` ("No estoy seguro", with the officer's contact) when there is no
  saved forecast, the forecast is more than 3 days old, it is 2-3 days old and the night looks cold, two or more
  weather inputs are missing, or the parcel has no station nearby and the cold end of the range reaches 0 °C.
  The contact in the demo pack is fictional.
- **Look.** One flat sheet and a road-sign verdict: a full-width band (red with a snowflake, yellow with a caution
  diamond, green with a check), the number, and a ruler that shows the likely range against the 0 °C line. Blue is
  the working colour (buttons, selection); red, yellow and green are kept for the three answers. A button next to
  EN switches light and dark; until it is used, the page follows the phone's setting. The font is Nunito
  (`fonts/nunito.woff2`, 39 KB, OFL) and the icons are Phosphor (MIT), inlined in `movil.js`. No network assets.
- **Updates.** The service worker serves one cache per version and replaces it as a whole set; the version is a
  hash of the page files. After editing anything under `static/movil`, run
  `uv run python scripts/build_movil_pack.py --stamp` (a test fails otherwise). On `localhost` the page reads the
  network first, so edits show at once; add `?sw=prod` to exercise the real update path.
- **Thermometer in the demo.** The demo pack carries the worked example of `backend/data/demo_logger.json`: SMN
  15390 (Jocotitlán), a station the held-out model never saw, standing in for a plot's thermometer. The tab lets
  you pick 0, 7, 14, 30 or 44 written-down nights and computes, on the phone, what the page would have said for the
  replay night, next to what was measured. One station and one season: an example, not proof.
- **Public link.** <https://m.helada.app/> (add `#demo` for the night of 14 Nov 2025), served from AWS (S3 and
  CloudFront, see `../infra/README.md`). Thermometer readings and damage reports go to `/api` on the same address.
  To publish a change: stamp, then deploy from `../infra`.
- **Demo on the phone.** The play button in the header (or `#tour`) walks the replay night by itself: the verdict, the
  ruler, what happened, a plot with no station ("No estoy seguro"), the thermometer and a damage report. The report it
  saves is never sent and is removed when the demo closes.
- **Languages.** Spanish is the product. `i18n.js` holds every screen text in Spanish and in English; the
  "EN" button (or `?lang=en`) is a reading aid for reviewers and changes the words on screen only. The English
  advisory texts come from `backend/advisory.en.yaml` and are marked as translations of the Spanish table, which
  is the one an agronomist signs. Damage reports are always sent to the server in Spanish.
- Not on the phone: voice notes and photos (they need the server's speech model), the loss packet PDF, and the
  planting advisor.

## Environment variables (all optional)

| Var | Default | Meaning |
|---|---|---|
| `HELADA_DATA_DIR` | `app/var` | SQLite DB, media, packets, cache |
| `HELADA_CHANNEL` | `sim` | `sim` \| `twilio_whatsapp` \| `sms` |
| `HELADA_OFFLINE` | `0` | `1` = never call Open-Meteo. The server repeats the forecast it saved for that night (computed with a connection, at most 3 days before the night) and marks it as saved. With nothing saved it has only a monthly average: no alert and no number go out, the farmer gets «No estoy seguro, pregunte a su técnico» with the phone to call |
| `HELADA_TTS` / `HELADA_TTS_VOICE` / `HELADA_PIPER_MODEL` | `auto` / `Paulina` / – | `auto`: piper (if the CLI and a `.onnx` voice are present), else macOS `say`, else no audio. `off` disables. Cached by text hash |
| `HELADA_ASR` / `WHISPER_MODEL` | `auto` / – | `auto` uses faster-whisper if it is installed **and** its weights are already on disk (`WHISPER_MODEL` only picks the size, default `small`); otherwise it uses the mock (reads a `<audio>.txt` sidecar, never invents text). `whisper` forces the real engine; `mock` forces the sidecar |
| `HELADA_LLM` / `HELADA_LLM_MODEL` | – / `qwen3:1.7b` | OpenAI-compatible base URL (for example `http://localhost:11434/v1` for Ollama, or llama.cpp `/v1`). Without it, a deterministic Spanish regex slot filler runs |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | – | Twilio credentials |
| `TWILIO_WHATSAPP_FROM` | `whatsapp:+14155238886` | Sandbox number |
| `TWILIO_SMS_FROM` | – | SMS sender, for the `sms` channel |
| `TWILIO_VALIDATE_SIGNATURE` | `1` | Verify `X-Twilio-Signature` on the webhook |
| `HELADA_PUBLIC_URL` | – | Public https base (for example an ngrok URL). Needed for signature validation behind a tunnel and for sending audio/PDF media via Twilio |
| `HELADA_SCHEDULER` | `1` | Background loop: sends queued alerts inside 18:00–20:00 and expires stale ones |
| `HELADA_TZ` | `America/Mexico_City` | |
| `HELADA_FORCE_STUB` | – | `1` ignores `helada_model` (tests use this) |
| `HELADA_SAMPLE_PIPER_MODEL` | – | A second piper voice for the built-in sample voice note of the farmer, where macOS `say` is not available |
| `HELADA_TTS_BUDGET_S` | `0` | Seconds of voice synthesis one «Enviar avisos» may spend; past it the remaining alerts go as text only. `0` = no limit |
| `HELADA_SEED_DIR` | – | Folder of voice notes made ahead of time (`scripts/seed_demo_media.py`), copied into the data dir at start |

### On-device small AI: voice-to-text and slot filling

Measured on 12 **synthetic** WhatsApp-style voice notes (macOS TTS, 3 with noise), CPU only, 4 threads. Full table, per-clip transcripts and caveats: [`docs/small-ai-bench.md`](../docs/small-ai-bench.md).

| Piece | Default | Disk | Peak RAM | Latency / note | Accuracy on the synthetic set |
|---|---|---:|---:|---:|---|
| ASR | faster-whisper **small** int8 (`WHISPER_MODEL=small`) | 486 MB | ~1.3 GB | ~1.1 s | WER 2.7% (base 14%, tiny 36%) |
| Slots | deterministic Spanish rules (`slots.py`) | ~30 KB | ~0 | <1 ms | 98% of slots right on ASR text, 0 wrong |
| Slots, optional | qwen3:1.7b via Ollama, behind the rules | +1.4 GB | +1.6 GB | ~1.4 s | same as rules (LLM alone: 56%, 44% wrong) |

- **Setup**: `bash scripts/setup_local_ai.sh`. It syncs the `asr` extra, downloads the whisper weights once, and, if Ollama is installed, pulls `qwen3:1.7b` and creates `helada-slots` (2k context, CPU only; see `scripts/ollama/Modelfile.slots`). It installs no system software: ffmpeg, uv and Ollama are prerequisites.
- **Run with real ASR**: `uv run --extra asr uvicorn backend.main:app` after the setup above. Plain `uv run` re-syncs without the extra, so keep `--extra asr`. `WHISPER_MODEL` only picks the size (default `small`). If the extra or the weights are missing, the mock ASR runs (sidecar `.txt`, never invents text).
- **Optional LLM**: add `HELADA_LLM=http://localhost:11434/v1 HELADA_LLM_MODEL=helada-slots`. Any OpenAI-compatible server with `response_format: json_schema` works (llama.cpp `llama-server` too).
  - `HELADA_SLOT_MODE` = `hybrid` (default when `HELADA_LLM` is set) | `llm` (LLM alone, benchmarking only) | `regex`.
  - Hybrid policy: the LLM may add `crop`/`cause` the rules missed; a crop/cause disagreement leaves the slot empty, so the bot asks a follow-up; `area_ha` and `date` always come from the rules; an LLM error or timeout falls back to the rules. The audit log's slot engine reads `llm+regex`, `llm+regex conflict=cause` or `regex(fallback)`.
  - qwen3 "thinking" is turned off with `reasoning_effort: "none"` (3-4x faster). Ollama's default 40k context uses ~6 GB RAM; the Modelfile's 2k context uses ~1.6 GB.
- **Why rules by default**: every 1-2B model tried (qwen3 1.7b/0.6b, gemma3 1b) was slower and got the arithmetic ("hectárea y media") and the calendar ("antier", "el lunes") wrong, and turned questions into frost reports. The LLM stays an optional recall helper, never the decider.
- **Re-run the benchmark**: `uv run --extra asr python scripts/make_test_clips.py` (macOS only; the clips are committed) and then `uv run --extra asr python scripts/bench_small_ai.py`. It writes `../docs/small-ai-bench.md` and `scripts/bench_results.json`.
- **Caveats**: the audio is TTS, not farmers, so field WER will be higher; record real notes before quoting accuracy. The whisper domain prompt (`asr.INITIAL_PROMPT`) was written while looking at these clips; with the old generic prompt `small` scored 10.7% WER. Latency is from an M3 Max; a Pi-class box is untested.

## Twilio WhatsApp sandbox steps (untested against a live account)
1. In the Twilio Console, go to Messaging › Try it out › WhatsApp sandbox. From the demo phone, send `join <code>` to +1 415 523 8886.
2. Run `ngrok http 8000`. Then:
   ```bash
   export HELADA_PUBLIC_URL=https://<id>.ngrok.app \
          HELADA_CHANNEL=twilio_whatsapp TWILIO_ACCOUNT_SID=… TWILIO_AUTH_TOKEN=…
   ```
3. Set the sandbox "When a message comes in" URL to `https://<id>.ngrok.app/webhooks/whatsapp` (POST).
4. Edit `backend/data/roster.json` and set one parcel's `phone` to the demo phone in E.164 (`+521…` or `+52…`; which one Twilio reports for Mexican mobiles is not verified). Restart.
5. Inbound voice notes and photos are downloaded with basic auth and processed in the background. Twilio's webhook times out after 15 s, so replies go out through the REST API. Audio and PDFs are sent as `MediaUrl` from `HELADA_PUBLIC_URL/files/...`.

## API
`GET /api/parcels` · `GET /api/forecast?date=&scenario=demo` · `POST /api/alerts/send {date, parcel_ids?, now?, override_window?, scenario?}` · `POST /webhooks/whatsapp` · `POST /api/sim/inbound` (form: phone, text | file | lat+lon, now?) · `GET /api/packets` · `GET /api/packets/{id}.pdf` · `GET /api/audit` · `GET /api/backtest`

Extras:
- `GET /api/config`
- `GET /api/alerts`
- `POST /api/alerts/flush`
- `GET /api/climatology/{id}`
- `GET /api/sim/messages?phone=`
- `POST /api/sim/sample` (what = audio | photo | location)

## «¿Cuándo siembro?» planting calendar
- Farmer writes or says "SIEMBRA", "¿cuándo siembro?", "¿qué variedad?" → Spanish text + voice note + a PNG table (`backend/planting.py`, deterministic, no LLM). Dashboard: "Calendario de siembra" in the parcel card. API: `GET /api/planting/{parcel_id}`.
- Inputs: `helada_model.climatology(parcel)` (first autumn frost p10/p50 = shelter Tmin ≤ 0 °C after 15 Jul, SMN 1991–2025) and the cycle table in `advisory.yaml` → `planting` (corto: V-54 A/V-55 A/HV 60 A; intermedio: H-51 AE/H-50/H-52; largo: criollos; INIFAP/ICAMEX sources S4, S7–S14), scaled +4% per 100 m above the source altitude (not verified in the field).
- Rule: latest sowing = first-frost p10 (risk-averse) or p50 − days to physiological maturity. The risk table uses a normal fit to p10/p50. Terrain-transfer parcels take the earlier frost of (their estimate, nearest SMN station).
- It says out loud that rainfed sowing depends on the rains: it helps choose the cycle and the date within the moisture window. Flagged "Requiere firma agronómica" until `meta.signed_by` is set.

## Inbound intent router (`backend/intents.py`, deterministic)
Every inbound text or voice transcript is classified by regex and keyword rules, per clause, with no model and no network. The labels are damage, opt-out, opt-in, forecast question, planting question, program question and greeting; a message can carry several. The precedence is **opt-out > opt-in > damage report > forecast > planting > program (PASACME) > greeting > other**.
- **A question never opens a loss report.** "¿Va a caer helada esta noche?" / "¿cómo viene la noche?" get tonight's forecast for the farmer's own parcel: Tmin, band, P(helada), and whether tonight's alert was sent or queued (the stored alert's numbers are used when there is one). "Program" gets the fixed PASACME facts (10-day notice, pre-registro, documents, 60%) and never says who qualifies. Greetings and anything unrecognised get a short menu: PRONÓSTICO · SIEMBRA · DAÑO · APOYO · BAJA.
- **A damage report must be a statement about the writer's own crop.** Questions, hypotheticals ("si se hiela…"), future ("va a caer helada"), negations ("aguantó", "no nos pegó"), someone else's field ("a mi compadre se le…") and text addressed to the system are not reports. A crop name alone is not a report either. Damage plus a question gets both: the answer, then the report's follow-up question. A question asked while a report is open is answered and the pending question is repeated; the report is not touched.
- **Opt-out / opt-in by a word, in any wording.** "BAJA/STOP/ALTO", "ya no me manden mensajes", "no quiero avisos", "bájenme de la lista" set `contacts.opted_out`, cancel alerts already queued for that phone, and log `contact.optout` (and `alert.cancelled`) in the audit chain. `service._deliver_alert` re-checks consent before any send. "ALTA" or "quiero volver a recibir los avisos" re-enables alerts (`contact.optin`). A complaint such as "ya no me llegó la alerta" is not an opt-out.
- **Cause.** Negation is handled ("no fue helada, fue granizo" → granizo; "no se heló" → no cause), and so are "bolitas de hielo" (hail), "se quemó la milpa" (frost, local usage), "se quemó con el sol" (drought) and fire. Two different named causes leave the slot empty, so the bot asks.
- **Pre-registro answer** → sí / no / unclear. Details are handled: "sí, desde marzo", "lo hizo mi esposa", "todavía no", "sí fui pero estaba cerrado". The first unclear answer ("¿qué es eso?", "creo que sí") gets one follow-up question. A second unclear answer prints **"Confirmar: pre-registro PASACME"** on the packet; a clear no prints "Falta: pre-registro PASACME".
- **Acceptance.** `tests/test_probe_sets.py` runs the message probe sets (`../evals/probes/*.json`, 145 team-written messages) offline against these rules, plus a post-hoc held-out set (`local_holdout_det.json`). The one known miss is a strict xfail with its reason. Run `uv run python tests/test_probe_sets.py` for the score table.

## How it works
- **Model adapter** (`backend/model_adapter.py`) is the only place that touches `helada_model`.
  - It imports it from the venv or `../model/src`. If it is missing or raises, a backup calculation (**TEMP_STUB**) answers:
    - raw Open-Meteo grid (`models=ecmwf_ifs025`, the model's training source, `elevation=nan`; model-suffixed hourly columns are handled), plus
    - a nocturnal lapse-rate, cold-pool (TPI) and clear-sky adjustment.
  - The stub makes no skill claim and no alert goes out on it: the farmer gets «No estoy seguro, pregunte a su técnico». The UI, the PDF and `/api/forecast` all say "STUB".
- **Alert rule** (`alerts.py`, pure function):
  - **ALERT** if `p_frost ≥ 0.30`, in any support regime. It sends text and a voice note.
  - **WATCH** if the parcel is terrain-transfer, `tmin_lo_c ≤ 0` and `p_frost < 0.30`. It sends softer text only, with no voice note, and does not count toward the cap.
  - Alerts are capped at 2 per parcel per rolling 7 days. Each parcel gets at most one message per night.
  - Send window is 18:00–20:00. Outside it, alerts are queued.
  - Opt-out works with BAJA/ALTA or natural wording (see *Inbound intent router* above).
  - The officer's "ignore window" override is audit-logged.
- **Alert text** comes from `advisory.yaml`: templates plus crop/stage actions, with the sources S1–S5 and PASACME filled from `../docs/advisory-table.md`.
  - The table is **unsigned**. The UI and the text alert show "Borrador sin firma agronómica" until `meta.signed_by` is set.
  - The SMS version is GSM‑7 with no accents, 160 characters or fewer.
- **Inbound flow**:
  1. Voice note → ffmpeg 16 kHz → ASR → slot filler {crop, area_ha, cause, date, notes}.
  2. For each missing slot, ask a follow-up (at most twice per slot).
  3. Ask for ≥4 photos, or the farmer says LISTO.
  4. Ask the pre-registro yes/no question.
  5. Build the packet.
- **Photo check** (Pillow EXIF): capture time between 0 and 10 days after the event, and GPS within 500 m of the parcel.
  - If there is no EXIF, it uses the receipt time and the shared WhatsApp location, and says so.
- **PASACME checklist** (`pasacme.py` + `pasacme.yaml`, from the 2024 Lineamientos, Gaceta 25‑jun‑2024) covers:
  - persona física, Delegación, ≤3 ha, crop and estimated stage, cause, date
  - the **10‑day notice clock**, **≥60% damage**, **≥4 photos**, pre-registro, one claim per plot per year
  - UGA and debts: "verificar en la Delegación"

  Output is only `Documentos completos` or `Falta: X`. A regex guard raises if any text looks like an eligibility decision. The footer reads: "…no sustituye la inspección. Lo decide la Secretaría del Campo."
- **Audit log**: `hash = sha256(prev_hash ‖ canonical_json(entry))`. SQLite triggers block UPDATE and DELETE, and `/api/audit` re-verifies the whole chain.
  - The log records alerts, inbound messages (media sha256), slots, and packets (PDF sha256). Each PDF prints the chain head.

## Unverified or untested
- Twilio WhatsApp and SMS sending, media download and live signature validation. The signature algorithm is unit-tested against itself only.
- Production WhatsApp Business: templates are needed outside the 24 h session, and the per-template cost for MX is unknown. Meta verification can take days.
- The MX number format Twilio reports (`+521` vs `+52`).
- llama.cpp `response_format: json_schema`: only tested through Ollama (0.18), not `llama-server`.
- The UGA restriction and SECAMPO debts cannot be checked (the UGA layer was not obtained). The 2026 PASACME changes (4 ha / $5,000) are not published.
- Advisory actions marked `TODO_AGRONOMO`. The whole table needs an agronomist's signature.

## Limitations
- **WhatsApp strips EXIF from photos sent as images.** On the real channel, the time and place checks usually fall back to the receipt time and a shared location. These checks raise the cost of fraud; they do not prevent it. The on-site inspection (Anexo 3) decides.
- **SMN observed Tmin in packets** comes from `backend/data/smn_obs_recent.json.gz` (16 roster stations, Jul 2023 on; built by `scripts/build_smn_obs.py` from the gitignored `../data/smn/obs_daily.csv.gz`). The packet prints the 08:00 reading that closes the night (plus the previous morning's, in case the farmer's date is off by one). When the SMN has not published it yet, it says "Observación SMN aún no publicada (rezago típico de esta estación: N …; último dato …)". The lag differs by station: about 1 week for most, 3–10 months for some (e.g. Enyeje 15026 stops at 2025-11-30). Re-run the script to refresh.
- **Alert volume and recall.** `p_frost ≥ 0.3` fires on about 7 of every 100 station-nights in the backtest.
  - At known stations it catches 49% of frost nights at 3.5% false alarms; at unseen sites, 30%.
  - `p_frost ≥ 0.2` would catch 73% at known stations, with about 15 alerts per 100 nights. That is a config choice for the humans.
- **Mock ASR**: recording your own voice in the simulator needs the speech setup (`scripts/setup_local_ai.sh`). Otherwise use the sample audio or type.
- **Mazahua**: `lang: maz` is stored but not used. There is no Mazahua keyword spotting yet.
- **Chat timestamps** in the simulator are real server time. «Hora del sistema» affects only the rule, date parsing and the packet.
- The phenological stage is estimated from the calendar month. When the farmer says it ("ya estaba jiloteando"), the packet also prints "etapa declarada por el productor" (maize only, regex). The inspection confirms it.
- **Packet folio numbering** is global, and there is no authentication. The dashboard is meant for a local network or laptop.
