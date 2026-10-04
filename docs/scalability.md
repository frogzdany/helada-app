# Scalability: what another place can reuse, and what comes next

**Short answer:** everything except the accuracy moves with one config file. Accuracy has to be earned with local observations. We ran the swap for potato in Puno, Peru (`docs/country-swap.md`).

## What moves as is, and what needs local data

| Moves as is (change `model/regions/<place>.yaml`) | Needs local data or local people |
|---|---|
| Forecast fetch and night aggregation (Open-Meteo works worldwide, in any timezone) | **Station offsets and the backtest.** Puno has no downloadable station data (SENAMHI requires registration), so Helada runs there as **forecast-only**, with no skill claim. |
| Alert rule (P ≥ 0.30, WATCH band), weekly cap, send window, crop-calendar gate | **Crop thresholds:** potato −1 °C air, bitter potato −3.5 °C. These are drafts until a local agronomist signs them. |
| WhatsApp/SMS channel, voice-note flow, opt-out by a word | **Voice in the local language:** Quechua and Aymara phrases recorded by local speakers. No speech-recognition claim. |
| Evidence-packet engine, "complete / missing X" output, hash-chained audit log | **The program's checklist:** in Peru, the Seguro Agrícola Catastrófico (S/ 800 per ha in the 2024-25 campaign, assessed by a perito ajustador) replaces PASACME. Its rules are not built. |
| Phone page and the 372 KB model format, offline | **Frost-date climatology for planting advice:** it needs the country's station series. |

**The test of the claim:**
- `uv run python -m helada_model.regions.demo puno --date 2025-01-13` runs the same code in Puno (Mazocruz: forecast −2.9 °C, band [−6.0, +1.0], P = 0.71 → ALERT). It is labelled "forecast-only" on screen.
- The Toluca model is deliberately **not** applied there: `predict()` refuses coordinates outside its training domain.

## Approximate cost

All figures are estimates. Prices and rates: `docs/tradeoffs-preconditions.md` §7.

| Item | Cost | Note |
|---|---|---|
| Loggers to earn accuracy: 5 per ejido or community, one frost season | ≈ MX$3,300 (≈ US$180) per ejido | IP65 Bluetooth logger + DIY radiation shield. Or ≈ MX$1,000 for 5 min-max thermometers that farmers read and text in. Mercado Libre prices, 29 Sep 2026 [V] |
| One small box per agriculture office (Pi class) | US$100–150, one time | Serves about 1,000 farmers |
| WhatsApp alerts | ≈ US$0.14 per farmer per season | 10 template messages at Meta's Mexico utility rate plus the provider fee. About US$0.45 if Meta bills them as marketing [V: confirm on Meta's rate card] |
| **Per farmer** | **≈ US$1.20 in year one, then ≈ US$0.15–0.30 per season** | Year one: messages ≈ US$0.14, box ≈ US$0.15 (1,000 farmers), loggers ≈ US$0.90 (an ejido of 200). Excludes extension and agronomist time, which is the largest cost |

## What comes next, after the event

1. **One ejido, one frost season.** Almoloya de Juárez (the most frost-hit municipality in the 2019 study), with 5 loggers, a consented roster and promotoras.
2. **Sign-offs:** an agronomist signs the advice table; the Secretaría del Campo agrees on how it receives a digital packet.
3. **Close the gaps in `docs/responsible-ai.md`:**
   - consent at enrollment;
   - deletion on request;
   - one phone pack per farmer;
   - Mazahua phrases recorded by native speakers.
4. **Measure:**
   - frost nights caught on logger parcels at a fixed false-alarm rate;
   - minutes to a complete packet;
   - share of alerts heard;
   - share of packets accepted for inspection.
5. **Retrain every September.** The forecast's bias moves from season to season.
6. **Second place:** Puno, through a SENAMHI data agreement with a Peruvian partner, or the same 5-logger kit.
