# Why AI, and not an SMS, a spreadsheet or a search

**Core idea.** At the parcel, the regional forecast is warmer than what actually happens. The model corrects it with what weather stations have observed, and it gives a calibrated chance of frost.

**Claim rule.** Skill numbers hold only for parcels near an SMN station (within 1.5 km and 50 m of elevation). Away from a station we say that the warm bias is removed and the range is wider, nothing more.

## In one paragraph

> The regional forecast treats the valley as one 25-kilometre grid cell. On clear, calm nights, cold air pools where the maize grows, and the forecast runs warm. Forwarded by text, it catches one frost night in four. A spreadsheet that subtracts each station's average error catches one in three. Helada's model learns how each site departs from the forecast on each kind of night, and gives a calibrated chance of frost. Near a station, it catches nearly six in ten at the same false-alarm rate. Away from a station, it only removes the warm bias and shows a wider range. Speech is the second piece: a small Whisper model turns a voice note into a report. Where AI didn't beat rules, we use rules. Guardrails: when the data runs short it says "I'm not sure, ask a person", and the packet never says "eligible".

## Two-row table: raw forecast vs Helada (parcels near an SMN station)

2025-26 frost season, which the model never saw in training: 39 SMN stations, 5,855 station-nights, 503 of them frost. Source: [`backtest.json`](../model/src/helada_model/artifacts/backtest.json) → `known_station.vs_open_meteo_default`.

| | Night Tmin error (MAE) | Warm bias | Error on frost nights | Frost nights caught at 5% false alarms | … at 10% false alarms |
|---|---|---|---|---|---|
| Regional forecast (Open-Meteo default, what a free app shows) | 2.65 °C | +1.54 °C | 5.49 °C | 24.3% | 40.0% |
| **Helada model** | **1.40 °C** | **+0.11 °C** | **2.36 °C** | **57.9%** | **76.9%** |

The 95% confidence intervals (bootstrap over nights) are 1.16–1.35 °C for the error gain and +26 to +43 points for frost nights caught at 5%.

**Away from a station** (80 held-out stations, 2024-25 and 2025-26): the error falls from 2.83 to 2.32 °C, but frost nights caught stay the same, 32.1% vs 32.4%. So the claim away from a station is only that the warm bias is removed and the range is wider, not that frost is detected better.

## The simpler tools, measured on the same nights

Same 5,855 station-nights near stations, 2025-26. The baseline here is raw ECMWF, the model's input, which is why the first row differs slightly from the table above. Source: [`model/research/out/ecmwf_ifs025_results.json`](../model/research/out/ecmwf_ifs025_results.json) → `K.metrics`. Recall is at a matched 5% false-alarm rate, so the comparison is equally fair to every method.

| Tool | What it does | MAE | Frost nights caught |
|---|---|---|---|
| **SMS that forwards the forecast**, best threshold | Sends the forecast as is | 3.00 °C | 24.7% |
| **SMS with a "frost if ≤ 0 °C" rule** | Alerts only when the forecast is below zero | 3.00 °C | **1.8%** (`backtest.json` → `known_station.raw.rule_tmin_le0`; 2.0% with the default forecast) |
| **Web search / weather app** | Same as the Open-Meteo default forecast | 2.65 °C | 24.3% |
| **Spreadsheet, one correction for the whole valley** | Subtracts the average error | 2.39 °C | 24.7%: no change, because a constant shift can't reorder nights |
| **Spreadsheet, one correction per station** | Subtracts each station's average error | 1.85 °C | 36.2% |
| **Per-station linear regression** (6 forecast inputs) | Classic statistical post-processing ("MOS") | 1.37 °C | 57.1% |
| **Helada (LightGBM + station offset, the product)** | One model for all parcels, calibrated probability | 1.40 °C | 57.9% |

**What this says, honestly:**

1. **Forwarding the forecast does not work.** By SMS or by app, it misses 3 frost nights in 4. A simple "below zero" rule misses 98 in 100, because the forecast almost never goes below zero on the nights it freezes.
2. **A fixed correction is not enough.** One per-station average in a spreadsheet helps (36%), but frost depends on the kind of night (clear, calm, dry), and an average can't see that.
3. **The gain comes from learning from local observations, not from one particular algorithm.** A per-station linear regression nearly matches our model near stations. That regression is itself a model learned from data, which is the point: the value is fitting how each site departs from the forecast, night by night. We ship one gradient-boosted model because it also:
   - serves parcels with no station history (the terrain-transfer regime: 2.32 vs 3.13 °C raw);
   - takes a parcel's own logger readings as they arrive (`calibrate_site`);
   - gives a calibrated probability with a range: the reliability table in `model/README.md`;
   - runs offline on the phone as one 372 KB file.
4. **Where AI does not beat rules, we use rules.** For reading a damage report, deterministic Spanish rules got 98% of slots right and 0% confidently wrong. A 1.7B local LLM got 56% right and 44% confidently wrong (`docs/small-ai-bench.md`, on 12 synthetic voice notes). The LLM is off by default. How far the rules are tested: they pass 325 of 326 checks on 145 team-written messages (`evals/probes/`, run by the test suite), but they were written against some of those messages, so that is a regression check, not accuracy. The one score taken before any tuning is 73 of 80 checks (91%) on 26 messages written after the rules were frozen; the misses were then fixed, so that set is no longer held out. None of these are real farmer messages. Speech recognition (Whisper `small`) is the one place where a neural model is needed, because no rule can turn audio into text.
