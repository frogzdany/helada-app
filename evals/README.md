# Message probe sets

Spanish messages a farmer could send, each with its context and the labels the rules must give. `app/tests/test_probe_sets.py` runs all of them offline against the deterministic rules in `app/backend/intents.py` and `app/backend/slots.py`.

| Files | What is checked | Labels |
|---|---|---|
| `j1_intencion.json`, `holdout_j1_intencion.json` | What the message is about | damage report, opt-out, forecast question, planting question, program question |
| `j2_prerregistro.json`, `holdout_j2_prerregistro.json` | The answer to "did you pre-register?" | yes, no, unclear |
| `j3_causa_cultivo.json`, `holdout_j3_causa_cultivo.json`, `bench_j3_causa_cultivo.json` | Cause of the damage and the crop | for example frost, hail, drought; maize, potato, oats |
| `j4_etapa.json`, `holdout_j4_etapa.json` | The crop stage the farmer states | for example flowering, grain fill |
| `local_holdout_det.json` | 26 messages written after the rules were frozen | all of the above |

Each case is `{"name", "state", "expect"}`: `state` holds the message (and the bot's pending question, if any), `expect` holds the label for each question.

**These are not real farmer messages.** The team wrote them, and the rules were written against some of them, so a pass is a regression check, not a measure of accuracy. See `docs/why-ai.md` and `docs/data-sources.md`.

Score table: `cd app && uv run python tests/test_probe_sets.py`.
