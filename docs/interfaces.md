# Interfaces and fixed rules

The interface between the frost model (`model/`) and the app (`app/`), the app's HTTP API, and the rules that are fixed code, not model output.

## Layout
| Dir | What | Stack |
|---|---|---|
| `model/` | Frost model | Python 3.12 package `helada_model` (uv), LightGBM/sklearn, numpy, pandas. Artifact < 1 MB. |
| `app/` | Channel, backend, dashboard, phone page | Python FastAPI, local-first (runs on a laptop / Raspberry Pi class box), SQLite. Static frontend. |
| `data/` | Cached public data (larger caches are gitignored and rebuilt by `model/research/`) | |

## Model interface (`helada_model`; the app imports it through `app/backend/model_adapter.py`)
```python
@dataclass
class Parcel:
    parcel_id: str; lat: float; lon: float
    elev_m: float | None = None          # filled from DEM if None
    owner_name: str = ""; phone: str = ""; municipality: str = ""; area_ha: float = 0.0
    crop: str = "maiz_temporal"; lang: str = "es"   # es | maz (stored; Mazahua is not supported yet)

@dataclass
class FrostForecast:
    parcel_id: str; date: str             # local night date YYYY-MM-DD
    grid_tmin_c: float                    # raw forecast at the grid cell (Open-Meteo)
    tmin_c: float; tmin_lo_c: float; tmin_hi_c: float   # parcel model, 80% interval
    p_frost: float                        # calibrated P(Tmin <= 0 °C)
    drivers: dict[str, float]             # e.g. {"elev_diff_m": 180, "tpi": -12, "clear_sky": 0.8}

@dataclass
class FrostClimatology:
    parcel_id: str
    last_spring_frost_doy_p50: int; last_spring_frost_doy_p90: int
    first_autumn_frost_doy_p10: int; first_autumn_frost_doy_p50: int
    frost_free_days_p50: int

helada_model.predict(parcels: list[Parcel], date: str, forecast: dict | None = None,
                     variant: str | None = None, sites=None) -> list[FrostForecast]   # variant: held-out model for replays; sites: thermometer calibrations
helada_model.climatology(parcel: Parcel) -> FrostClimatology
helada_model.backtest_report() -> dict        # MAE raw vs model, frost recall/precision at matched FAR, n, seasons
```

## App HTTP API
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/parcels` | demo roster (synthetic names; real coordinates in the Toluca valley) |
| GET | `/api/forecast?date=` | FrostForecast per parcel + raw grid forecast |
| POST | `/api/alerts/send` | render alert (text + voice note audio) per parcel, send via channel adapter; respects weekly alert cap |
| POST | `/webhooks/whatsapp` | inbound (Twilio WhatsApp format): voice note / photo / text |
| POST | `/api/sim/inbound` | same as webhook, from the built-in phone simulator (demo without Twilio) |
| GET | `/api/packets` / `/api/packets/{id}.pdf` | loss-evidence packets |
| GET | `/api/audit` | append-only audit log (hash-chained) |
| GET | `/api/backtest` | backtest numbers shown in the Model evidence tab |

Channel adapters: `sim` (default, in-browser phone), `twilio_whatsapp` (sandbox), `sms` (Twilio SMS fallback). Env: `HELADA_CHANNEL`, `TWILIO_*`, `HELADA_LLM` (local llama.cpp/Ollama URL, optional), `WHISPER_MODEL` (whisper.cpp / faster-whisper small, optional).

## Deterministic core rules
- Alert rule: **ALERT** if `p_frost >= 0.30` (any support regime): text + voice note, max 2 alerts per parcel per 7 days. **WATCH** if support is `terrain-transfer` and `tmin_lo_c <= 0` and `p_frost < 0.30`: softer text, no voice note, does not count toward the cap. Both one per parcel per night, evening send window 18:00–20:00. (The old `or tmin_lo_c <= 0` alert trigger was dropped: it fired on 25–29 of every 100 station-nights.)
- Advisory table (YAML, each action cited to its source, see `docs/advisory-table.md`; agronomist sign-off is a stated precondition).
- PASACME checklist (Edomex crop-loss support: ≤ 3 ha, individual producer, one claim per plot per year, eligible causes drought/frost/hail/flood, not in restricted UGA). Output is only "complete" / "missing: X". NEVER "eligible". Footer: "Lo decide la Secretaría del Campo."
- Loss-report slots {crop, area_ha, cause, date, notes} are filled from the transcribed text by deterministic Spanish rules; anything unclear → ask a follow-up question. Optional and off by default (`HELADA_LLM`): a 1–2B grammar-constrained LLM may only suggest a missing crop or cause, never area or date; a disagreement with the rules empties the slot, and anything out of schema is dropped.
