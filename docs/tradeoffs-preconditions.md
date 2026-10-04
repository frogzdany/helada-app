# Trade-offs, preconditions, risks, inclusivity

What Helada gives up, what must be true before it can work, its risks, who it may leave out, how data is handled and what it costs. [V] = unverified.

## 1. Honest actionability
For maize in flower, a warning at 7 pm rarely saves the crop. Helada is built for three things that do help:

1. **Planting date and variety cycle** chosen from each parcel's own frost calendar (last spring frost, first autumn frost). Main lever. Early-cycle INIFAP varieties (135-140 days) exist for late plantings (`docs/advisory-table.md`, S4).
2. **Protectable crops** the same night: potato, vegetables, oat seedlings, open-field flowers.
3. **Fast, complete loss documentation**: PASACME requires notice to the Delegación within 10 calendar days and at least 60% damage verified by on-site inspection (`docs/pasacme.md`).

Where it does not help: maize at flowering or grain fill (FAO critical -1 to -2 °C at flowering). The alert says so.

## 2. Trade-offs
| Choice | We gain | We give up |
|---|---|---|
| Parcel-level model learned from SMN station records (186 stations in the data, 83 in the training table) | Corrects the grid-cell warm bias in hollows and high ground | Few stations above 2,800 m: wider intervals there, shown as ranges, not hidden |
| Small model (LightGBM, 372 KB) | Runs offline, also on the farmer's phone; explainable drivers (elevation gap, TPI, clear sky) | Cannot learn from images or dense radar; skill limited to radiative frost nights |
| WhatsApp voice notes | Users already have it; unlimited WhatsApp bundle on Telcel prepaid from about MX$10 [V] | Depends on a Meta/Twilio channel and templates. Alerts cannot go out from a personal SIM: Telcel's fair-use terms ban mass alerts from a personal line |
| Checklist that never says "eligible" | Avoids false promises and legal exposure | Some users will want a yes/no; the packet says only "Documentos completos" or "Falta: X" |
| Alert cap (max 2 per parcel per 7 days, 18:00-20:00) | Limits fatigue | May skip a marginal night |
| Backtest on held-out stations and seasons | Credible skill claim | Small n: the 2024+ Previous Runs record is about 2.5 years, two frost seasons (`docs/data-sources.md`). ERA5 results (Puno only) are reported separately, with no skill claim |
| Open-Meteo free tier | Zero setup | Non-commercial terms, so a real deployment needs a key or a self-hosted copy [V] |

## 3. Preconditions
1. A parcel roster with coordinates from the comisariado ejidal or a program registry, with the owner's consent.
2. Secretaría del Campo willing to accept digital pre-filled packets. Today it verifies by on-site inspection with Anexo 3. Helada pre-fills the inputs for that inspection and tracks the 10-day notice clock; it does not replace the inspection.
3. A verified WhatsApp Business number and a template budget. Verification can take days. The demo uses the built-in phone simulator; sending through the Twilio sandbox is untested on a live account.
4. **An agronomist signs the advisory table** (INIFAP/UAEMex/Tec). Until signed, the UI shows "Borrador sin firma".
5. At least one moment a day with signal in the village; alerts queue otherwise. Coverage in the valley was not mapped and must be checked per community (`docs/problem-evidence.md`, Gaps).
6. Farmer consent for data (see §6).
7. **Loggers on off-station parcels, and someone who keeps them reading.** Measured on 80 SMN stations the model never saw (`model/README.md`, "Logger learning curve"):
   - Readings cut the Tmin error from **2.32 °C** to **1.79 °C after 30 nights** and **1.56 °C after 60 nights**. That is the station-anchored level (1.55 on the same nights).
   - Frost detection improved reliably only with **a full season** of readings. With a previous season: recall at 5% false alarms 34% → 45% (station 56%). Within the first season it rose in 2024-25 but *fell* in 2025-26.
   - So the pilot is a full frost season. Until 150 nights, logger parcels keep the "vigilar" safety net.
   - Built: officer upload of CSV or pasted readings, and the farmer texting «anoche marcó -2» (confirmed with sí/no, audit-logged).

## 4. Risks
- Away from a station the model does not detect frost better than the default forecast (32.1% vs 32.4% of frost nights caught at 5% false alarms), although the Tmin error falls from 2.83 to 2.32 °C. There Helada removes the warm bias and shows a wider range, until loggers are installed. Near a station (39 stations, 5,855 station-nights, the 2025-26 season, never seen in training) the error fell from 2.65 to 1.40 °C and frost nights caught rose from 24.3% to 57.9% (`docs/why-ai.md`).
- Packets can be gamed (edited photos, wrong plot). EXIF time and GPS checks plus the station Tmin raise the cost. WhatsApp strips EXIF from photos sent as images, so on the real channel these checks usually fall back to the receipt time and a shared location (`app/README.md`). The inspection stays the ground truth.
- False negatives on a frost night (trust loss) and false positives (alert fatigue). We report recall at a matched false-alarm rate, not accuracy.
- The 2026 program rule change reported by the press (MX$5,000 per ha, 4 ha) is not published. The amount and the cap are config values, not hard-coded (`docs/pasacme.md` §2).
- Logger data quality: a moved, unshielded or sun-exposed sensor, or a thermometer not reset each evening, makes the learned offset wrong. Mitigations:
  - range checks and a sí/no confirmation of each WhatsApp reading;
  - the audit log keeps every reading;
  - a field check by the promotora at install and mid-season (a process, not built).
- Partial-season loggers can *lower* frost recall (measured in 2025-26). The WATCH safety net stays until a full season.
- Model decisions are opaque to farmers. Mitigation: the range (for example 0.8 °C ± 1.2) in plain words, and the drivers.

## 5. Inclusivity
- **Literacy:** voice-first. The alert is a voice note in Spanish (TTS), with one to two actions, under 20 seconds; text is the fallback. Icons on the dashboard for extension staff. It has not been tested with real farmers.
- **Voice input:** Spanish speech recognition (Whisper `small`) was measured only on synthetic voices. Rural accents and crop names are expected to do worse; by how much is not measured. The slot-filler asks a follow-up whenever a field is unclear and never guesses the area or the cause.
- **Mazahua: not supported yet.** Nothing is built: a parcel can carry `lang: maz`, but alerts go out in Spanish. The plan (`docs/responsible-ai.md` §5) starts with phrases recorded by native speakers, then keyword spotting for 5-10 words (helada, granizo, se quemó, hectárea) tested with them. Meta MMS lists an ASR adapter (`maz`) and a TTS model (`mms-tts-maz`, CC BY-NC 4.0); both listings were checked by API. Quality on Edomex Mazahua is unmeasured. Otomí of Edomex (Temoaya) has only language identification in MMS. No indigenous-language coverage is claimed: nothing has been tested with native speakers.
- **Women farmers who do not own a phone / shared phones:** many farmers are women and many phones are shared or belong to a spouse or child [V]. National figures (`docs/problem-evidence.md`, rows 6, 8 and 9): 17% of the producers who run a farm are women; 15.4% of people aged 6+ do not use a mobile phone on their own; phone use is almost equal by gender (women 84.5%, men 84.8%). There is no figure for rural Edomex. The program's priority order puts women heads of household and indigenous communities first (PASACME 9.3.2). Design: (1) the alert goes to a named contact phone per parcel, which can be a relative or the ejido comisariado; (2) the packet is under the farmer's name, not the phone owner's; (3) no assumption that the phone owner is the decision maker. Not done: a review of the alert texts so that the lock-screen preview shows no personal detail. Outreach by promotoras and comisariado, not only WhatsApp, is a process, not software.
- **Connectivity:** 97.0% of phone users use a smartphone (INEGI, ENDUTIH 2025, national; `docs/problem-evidence.md` row 6), but rural coverage has gaps. The phone page works with no signal and keeps readings and reports until it returns. An SMS fallback for alerts is in the code but untested on a live account.
- **Age:** the priority group includes adults over 60. Alerts are voice notes, and replies can be voice notes. A large-print packet is not built: the PDF body text is 9.5 pt (`app/backend/packet.py`).

## 6. Data governance
- Collected: name, phone, municipality, parcel coordinates, area, crop, voice notes, photos. Purpose: frost alerts and loss packets only.
- Consent: one word («BAJA») stops every alert, and the opt-out is logged. Not built: a consent step at enrollment, and deletion on request. Opting out stops messages; it does not delete records (`docs/responsible-ai.md` §2).
- Legal basis in Mexico, not settled: LFPDPPP (private) or, for the state program, Ley de Protección de Datos Personales en Posesión de Sujetos Obligados del Estado de México y Municipios (as cited in PASACME sec. 9.1). Which one applies to a non-governmental operator is not confirmed [V].
- Local-first storage (SQLite on the box). The hosted demo keeps the same data in the team's own AWS account (DynamoDB and S3; `docs/aws-architecture.md`).
- What leaves the box (`docs/responsible-ai.md` §1): WhatsApp messages, through Twilio and Meta; parcel coordinates, without names, sent to Open-Meteo to fetch the forecast; map tile requests from the dashboard to OpenStreetMap. The loss packet (PDF) goes to the farmer in the chat, and the farmer decides whether to take it to the Delegación.
- Append-only, hash-chained audit log for each packet.
- Planned, not built: voice notes deleted after transcription unless the farmer opts in (today they are kept on the box); coordinates rounded in any published dashboard.
- No ID numbers, CURP or program folios are asked for or stored.
- The demo roster is synthetic: its names and phones are fictional.

## 7. Costs
### WhatsApp Business messaging in Mexico [V]
- Meta charges per delivered template message since 1 July 2025. Non-template messages and utility templates inside the 24-hour customer-service window (after the farmer messages first) are free. Source: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing
- Mexico has standalone rates. Meta says lower utility/authentication rates applied 1 July 2025 and lower marketing rates 1 January 2026 (same doc). We could not open the CSV rate card. One third-party table quotes Mexico at **US$0.0397 marketing, US$0.0085 utility, US$0.0085 authentication, effective 1 October 2026**: https://whautomate.com/whatsapp-business-api-pricing. These rates are not confirmed against Meta's own rate card [V].
- Category risk: a frost alert may be classed as marketing if it reads as promotion. It is meant as an account or safety utility update, but Meta may reclassify it.
- Worked estimate, assuming 1,000 farmers and 10 alerts per season: 10,000 template messages. Utility at $0.0085 = US$85; marketing at $0.0397 = US$397. Per farmer per season: US$0.09 (utility) to US$0.40 (marketing) [V]. If the farmer replies within 24 hours, follow-ups are free.
- Not included: the BSP fee if Twilio is used (a per-message markup [V]) and the number's monthly cost. The demo uses the built-in phone simulator, at no message cost. The Twilio sandbox has no template cost [V]; sending through it is untested on a live account.

### Parcel loggers (5 per ejido)
Mercado Libre MX listings checked 2026-09-29, USD at MX$18.5. Reseller prices change often. [V] = not verified or not a Mexican source.

| Option | Unit price | Key spec | 5 per ejido |
|---|---|---|---|
| **Min-max thermometer, read by the farmer each morning and texted on WhatsApp** («anoche marcó -2») | Generic U-tube MX$162–213 [V: accuracy unknown]; TFA 10.4001 bimetal MX$620; Taylor 5460 MX$1,169 | No electronics. Someone must read and reset it daily; the reading is confirmed by sí/no in the chat. | **≈ MX$1,000** (generic) to MX$3,100 (TFA) |
| **SwitchBot Indoor/Outdoor Thermo-Hygrometer (IP65)** + DIY shield + lithium AAA | MX$487–985 + ≈ MX$120 shield [V: estimate] + ≈ MX$60 | IP65; −20 °C on stock batteries, −40 °C on lithium; 68 days on the device, 2 years in the app; Bluetooth to a phone | **≈ MX$3,335** (≈ US$180) |
| Elitech RC-5 / RC-5+ USB logger + DIY shield | MX$649–747 | −30 to 70 °C, ±0.5 °C, 32,000 points, CR2032 about 6 months; needs a laptop to download [IP rating V] | ≈ MX$3,845 |
| Govee H5100 (3-pack) + DIY shield | MX$937 for 3 | Rated indoor; −20 °C floor not confirmed [V] | ≈ MX$2,500 [V] |
| iButton DS1921G/DS1922L + reader | ≈ MX$1,057–2,250 each + ≈ MX$895 reader (US distributors) [V] | Not sold on ML MX | > MX$6,000; not recommended |

- **Radiation shield:** an unshielded sensor reads several °C high in sun and roughly 1–2 °C low on clear, calm nights (a textbook figure, not checked [V]).
  - Shield options: commercial ML 6-layer shield MX$461–549; AcuRite 06054M MX$870–978; Davis 7714 ≈ US$130–145 [V].
  - The calibration learns a *consistent* bias, but the shield must stay the same all season.
- **Recommendation for the pilot:** SwitchBot IP65 + DIY shield on 5 parcels (≈ MX$3,300 per ejido, one time). Add min-max thermometers wherever a farmer will text a reading each morning.
- **Sources:**
  - https://listado.mercadolibre.com.mx/termometro-maxima-minima-exterior_OrderId_PRICE_NoIndex_True
  - https://www.mercadolibre.com.mx/termometro-maxima-y-minima-interior-y-exterior-tfa-dostmann/up/MLMU418360274
  - https://www.mercadolibre.com.mx/termometro-interiorexterior-maximaminima-reset-5460-taylor/p/MLM2107447716
  - https://www.mercadolibre.com.mx/switchbot-wireless-hygrometer-thermometer-ip65-120m-range/p/MLM2049786613
  - https://us.switch-bot.com/pages/switchbot-indoor-outdoor-thermo-hygrometer
  - https://www.mercadolibre.com.mx/termometro-digital-elitech-rc-5-usb-datalogger-rango-30-a-70c/p/MLM26207213
  - https://www.mercadolibre.com.mx/thermometer-hygrometer-govee-h5100-bluetooth-3-pack/p/MLM2063728168
  - https://www.mercadolibre.com.mx/termometro-higrometro-digital-bluetooth-govee-h5075/p/MLM2100989376
  - https://www.mercadolibre.com.mx/higrometro-bluetooth-de-temperatura-y-humedad-inkbird/p/MLM2101521328
  - https://www.mercadolibre.com.mx/solar-radiation-shield-outdoor-plastic-louver-case-shell/p/MLM2102483178
  - https://www.mercadolibre.com.mx/acurite-0605-4-m-escudo-de-radiacion-solar-de-temperatura/up/MLMU5306403008
  - https://www.ibuttonlink.com/collections/ibuttons/products/ds1922l
  - https://davisinstruments.com/collections/installation-accessories/accessory-type_radiation-shields

### Hardware
- One Raspberry Pi 5 (8 GB) plus case, power and SD/SSD: roughly US$100-150 [V]. The model (< 1 MB), SQLite, and the FastAPI backend fit easily. Whisper small, and the optional 1-2B LLM if it is switched on, have not been measured on a Pi: the demo runs them on a laptop CPU (about 1.1 s per voice note). No Pi latency is claimed.
- Station data and Open-Meteo pulls: free.
- Human cost dominates: extension staff time and the agronomist review.

## 8. Small AI
The frost model is 372 KB, and it also runs on the farmer's phone with no signal. Speech to text is Whisper `small` on one small box in the agriculture office. A damage report is read by fixed Spanish rules, because the small LLMs we tested were confidently wrong too often: the 1.7B model alone got 44% of slots confidently wrong (`docs/small-ai-bench.md`).

- Sizes, measured (`docs/small-ai-bench.md`, `model/README.md`): frost model 372 KB; terrain 865 KB; Whisper `small` int8 486 MB (244M parameters); slot rules about 30 KB. The optional LLM (`qwen3:1.7b`, +1.4 GB) is off by default.
- Deterministic core does the high-stakes steps (thresholds, cap, checklist) and reads the damage report. If the optional LLM is switched on, it may only suggest a missing crop or cause, never area or date, and it never decides.
- The country swap (Puno) reuses the same pipeline with a different config file. It runs there forecast-only, with a band measured against ERA5 and no skill claim: station data needs SENAMHI registration (`docs/country-swap.md`).

## 9. Development outcome
- Livelihoods: smallholders with up to 3 ha (the program's own definition); PASACME reached about 6,720 producers and 10,900 damaged ha from 2024 to Q1 2026 (reported by state press; see `docs/pasacme.md`), so documentation speed matters at that scale.
- Speed: the measure is "time to a complete packet". The target is under 5 minutes in the demo. The baseline, the farmer's current trip to a window with paper documents, has not been measured [V].
- Poverty context: San Felipe del Progreso 27.0% extreme poverty in 2020 versus 8.5% nationally (CONEVAL). This is a national poverty measure, not the World Bank $3/day line.
- Jobs: extension agents and promotoras as the human interface (not replaced).
