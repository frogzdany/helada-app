# Country swap: Puno, Peru (potato frost)

**Claim:** the pipeline, the channel, the alert rule, the packet engine and the audit log move to a new country when you change one config file. The model's accuracy does not come along with it. Accuracy comes from local observations. Puno has none we can download (SENAMHI requires registration), so in Puno Helada runs in an honest **forecast-only** mode. It shows the forecast, a band measured against reanalysis, and a stated precondition. It makes no skill claim. This is what "a protocol, not a product" means here.

Verified 2026-09-29. Items marked [V] come from search summaries or secondary sources that we could not open or confirm.

## Run it

```sh
cd model
uv run python -m helada_model.regions.demo puno --date 2025-01-13     # potato in the field: an alert night  
uv run python -m helada_model.regions.demo puno --date 2025-07-10     # cold season: potato not in the field, so no crop alert
uv run python -m helada_model.regions.demo puno --date 2025-01-13 --crop papa_amarga
uv run python -m helada_model.regions.demo toluca --date 2025-11-14   # same runner, trained model (network)
uv run python regions/puno_eval.py                                    # re-pull and re-measure (network, ~40 calls)
```

- Configs: `model/regions/toluca.yaml`, `model/regions/puno.yaml`. Loader: `helada_model.regions.load(name)`.
- Puno nights from 2024-03-01 to 2026-09-19 are cached in `model/regions/out/puno_nights.csv.gz`, so the Puno demo runs offline for those dates. Other dates use the Open-Meteo API.
- Tests: `model/tests/test_regions.py`. Network calls are mocked.

> **No accuracy claim for Puno.** We do not say that Helada predicts frost in Puno better than the forecast, and no Puno error figure is an accuracy figure: there are no station observations to measure against.

## What we measured in Puno (reanalysis, not observations)

**Setup:**
- **Points.** 10 points near district capitals around Lake Titicaca and on the northern and southern Altiplano: Ilave, Juli, Yunguyo, Chucuito, Capachica, Huancané, Azángaro, Lampa, Ayaviri and Mazocruz.
  - Elevation 3,826–3,971 m. Each point sits in its own 0.25° forecast cell.
- **Forecast.** Open-Meteo Previous Runs `ecmwf_ifs025`, `*_previous_day1`. This is the same source and lead time that the Toluca model was trained on.
- **Reference.** Open-Meteo archive **ERA5**, with **ERA5-Land** as a check.
- **Night.** 18:00–08:00 America/Lima.
- **Seasons.**
  - *Cold season*: May–Aug 2024 and 2025. This is the climatological frost season, when the potato crop is not in the field.
  - *Crop season*: Oct 15–Apr 30 of 2024-25 and 2025-26. This is when potato is in the field.

All numbers below come from `model/regions/out/puno_eval.json`.

| Measured | Cold season (2,460 point-nights) | Crop season (3,960) |
|---|---|---|
| Day-1 forecast vs ERA5: MAE / bias (forecast − ERA5) | 1.83 °C / +1.11 °C | 1.19 °C / −0.42 °C |
| ERA5-Land vs ERA5 MAE: how far the two reanalyses disagree with each other | 1.74 °C | 1.07 °C |
| Helada 80% band width (forecast-vs-ERA5 spread + site prior) | 7.7 °C | 7.0 °C |
| Same band without the site prior (reanalysis residuals only) | 5.0 °C | 3.7 °C |
| Cross-season stability of the reanalysis-only 80% band (fit on one season, test on the other) | 0.81 / 0.67 | 0.80 / 0.81 |
| Alert rule at −1 °C (papa): ALERTs / WATCHes per 100 point-nights (in-sample) | not sent (no crop) | 1.6 / 4.2 |
| ERA5 nights ≤ −1 °C per 100 point-nights | 30.6 | 0.7 |

**How to read this:**
1. **Agreement with ERA5 is not skill.**
   - ERA5 comes from an older version of the same ECMWF model at about 31 km.
   - The two reanalyses disagree with each other almost as much as the forecast disagrees with either one (1.74 vs 1.83 °C in the cold season).
   - We therefore use ERA5 only to put a *floor* under the band.
2. **Grid-scale products cannot see Altiplano station minima.**
   - Over 2.5 years and 10 points, the coldest forecast night was −5.2 °C and the coldest ERA5 night was −6.7 °C.
   - For 4–8 July 2025, SENAMHI forecast minima down to −8 °C in the Puno highlands and −14 °C in the southern highlands ([Infobae, 4 Jul 2025](https://www.infobae.com/peru/2025/07/04/puno-se-congela-senamhi-pronostica-temperaturas-de-hasta-8-grados-bajo-cero-durante-los-proximos-cinco-dias/)).
   - On those nights our points ranged from −4.1 to +6.3 °C (forecast) and from −5.1 to +5.3 °C (ERA5).
   - This is the Toluca finding again, only larger: the parcel is colder than the grid, and only local observations can measure by how much.
3. **The site prior (SD 2.3 °C) is borrowed from Toluca.** It is the spread of per-station forecast offsets across SMN stations (see `model/README.md`). In the Altiplano it is probably larger, because radiative cooling at 3,900 m is stronger. So treat the band as a lower bound on the true parcel uncertainty.
4. **Two numbers show the band is roughly stable, not that it is right:**
   - the crop-season coverage of 0.80 and 0.81;
   - the cold-season 0.67, which reflects a season-to-season shift in the forecast-minus-ERA5 bias (+1.6 °C in 2024, +0.6 °C in 2025).

## Why the Toluca model is not applied in Puno

- The GBMs were trained on SMN shelter readings from 18.9–20.1° N, 2,300–3,100 m, with Oct–Mar nights. Puno is at 15–17° S and 3,800–4,000 m, and its cold season is May–Aug.
  - Latitude, longitude, day-of-year and terrain inputs all fall outside the training range, and tree models extrapolate flat.
  - The DEM artifact covers Toluca only.
  - `helada_model.predict()` raises `ValueError` outside its domain on purpose.
- The Toluca result also argues against reusing a model elsewhere without local data. At unseen Toluca sites, *inside* the training region, terrain features recovered only about 10% of the station-offset gap (`model/README.md`).
- **The Puno support label is `forecast-only`, not `terrain-transfer`.** Toluca's terrain-transfer regime was validated on held-out Toluca stations. Nothing has been validated in Puno.

## What transfers and what doesn't

| Component | Transfers? | Notes |
|---|---|---|
| Night aggregation, forecast fetch (Open-Meteo, any timezone) | Yes | Same code (`features.nights_from_hourly`). The timezone comes from the config. |
| Region config: bbox, crops, thresholds, sources, languages, program | Yes, by design | `model/regions/*.yaml`, validated on load |
| Alert rule (ALERT if P ≥ 0.30, WATCH if the band reaches the threshold) | Yes | The threshold is per crop: 0 °C air for maize, −1 °C air for potato, −3.5 °C for bitter potato (all drafts; see below) |
| Crop calendar gate | Yes | No crop alert when the crop is not in the field. July in Puno is chuño season, not potato season. |
| WhatsApp / SMS channel, weekly alert cap, send window | Yes | Coverage must be checked per community. OSIPTEL "Checa tu señal": https://checatusenal.osiptel.gob.pe/ [V] |
| Evidence packet engine, hash-chained audit log | Yes | The engine transfers. The checklist content does not. |
| Station-anchored offsets, calibration on observations, the backtest | **No** | They need SENAMHI data or local loggers |
| Terrain-transfer model | **No** | Trained in Toluca and not validated anywhere else |
| Frost-date climatology for planting advice | **No** | Built from 1991–2025 SMN records. Puno needs SENAMHI series. |
| PASACME checklist (≤ 3 ha, individual, UGA) | **No** | Replaced by the SAC rules below. Not built. |
| Spanish alert text / voice | Partly | Spanish works. Quechua and Aymara voice notes must be recorded once by local speakers. |
| Speech recognition | **No claim** | Meta MMS-1b-all lists `ayr` (Central Aymara) and `quz` (Cusco Quechua) but not Puno Quechua ([language list](https://dl.fbaipublicfiles.com/mms/asr/mms1b_all_langs.html)). We have not tested either one. Replies use buttons or Spanish. |

## Crop thresholds (draft; an INIA Puno agronomist must sign them off)

| Crop | Plant critical temperature | Alert on air Tmin | Source |
|---|---|---|---|
| Potato (*S. tuberosum*, improved and sweet native varieties) | Most cultivated varieties are sensitive below **−2.5 °C**. FAO gives −2/−3 °C at emergence and −1/−2 °C at flowering. | ≤ −1.0 °C | Marmolejo & Ruiz (2018), *Scientia Agropecuaria* 9(3), [SciELO](http://www.scielo.org.pe/scielo.php?script=sci_arttext&pid=S2077-99172018000300010); FAO (2010) *Protección contra las heladas*, Table 4.5, [PDF](https://www.fao.org/4/y7223s/y7223s05.pdf) |
| Bitter potato (*S. juzepczukii*, *S. curtilobum*) | Tolerates **−4.8 to −5.5 °C** | ≤ −3.5 °C | Bonifacio, Ramos, Alcon & Gabriel (2013), *Revista Latinoamericana de la Papa* 17(2), citing Li & Palta (1977) and Bonifacio (1991). [PDF](https://dialnet.unirioja.es/descarga/articulo/5512095.pdf) |

- **How the air threshold is set:** the plant threshold plus a margin of about 1.5 °C, because the canopy runs colder than a 2 m shelter on clear nights.
- **Open question on the sources:** neither source says whether its figure is leaf temperature or air temperature. The margin is a draft that an agronomist must confirm.
- **Crop calendar (MIDAGRI 2024, *Observatorio de siembras y perspectivas de la producción: Papa*, [PDF](https://cdn.www.gob.pe/uploads/document/file/6103503/4344772-observatorio-de-siembras-y-perspectivas-de-produccion-papa.pdf)):**
  - Nationally, potato is sown mostly Aug–Dec, with a cycle of about 6 months.
  - Puno produced 16.5% of Peru's potatoes in 2022 (999,000 t).
  - 85.6% of Puno's potato area is grown for home consumption.
  - The config's in-field window for Puno, Oct 15 – Apr 30, is an approximation for the Altiplano [V: confirm per province with the Agencia Agraria].

## Peru's loss program: Seguro Agrícola Catastrófico (SAC)

Peru's counterpart to Edomex's PASACME is the **Seguro Agrícola Catastrófico** from MIDAGRI:
- **Funding and cost to farmers.** It is funded 100% by the State through **FOGASA** and is free for the farmer.
- **Perils.** **Helada y baja temperatura** is among the covered perils, alongside drought, hail, excess rain and pests.
- **Payout (2024-25 campaign, Aug 2024 – Aug 2025).** **S/ 800 per ha** for prioritised crops and S/ 400 per ha for others, with a **maximum of 10 ha** per beneficiary.
- **Loss assessment.** A *perito ajustador* assesses the loss together with an *agente agrario* and signs an *acta de ajuste* ([Andina, 29 Dec 2024](https://andina.pe/agencia/noticia-eres-agricultor-seguro-agricola-te-compensa-s-800-hectarea-973171.aspx)).
- **Puno, 2023-24 campaign.** About 2,747 ha were indemnified for 8,953 producers ([Agraria.pe](https://agraria.pe/noticias/midagri-activa-seguro-agricola-catastrofico-para-la-atencion-38266)).

**What Helada would change:** a SAC packet would help the ajustador by supplying the parcel, the date, GPS, photos, and the modeled and reference Tmin. The packet reads "complete / missing X", never "eligible".

**Not built:**
- the SAC trigger rules;
- which sectors are covered;
- the claim deadlines.

All three must be read from the current MIDAGRI norm before any packet is shown [V].

## Existing warnings: partner, don't duplicate

SENAMHI Puno already issues frost and *friaje* warnings at levels 1–4 ([Heladas y Friajes Avisos](https://www.senamhi.gob.pe/main.php?dp=puno&p=heladas-y-friajes-avisos)). In Puno, Helada's job is:
- **last mile:** put the warning on WhatsApp, in the farmer's language, with a crop-calendar gate;
- **parcel-level evidence:** document losses for SAC.

It is not to compete with SENAMHI's regional forecast. The shortest route to parcel accuracy is a data agreement with SENAMHI. The cheaper route is loggers.

## Languages and channel in Puno

- **Mother tongue (INEI Censo 2017):** Quechua 42.9%, Spanish 28.0%, Aymara 27.0% [V: INEI press summary]. Aymara is concentrated on the south and west shores of the lake (Chucuito, El Collao, Yunguyo, Puno) [V].
- **Voice:** alert voice notes need to be recorded by local speakers, one set per language. There is no text-to-speech (TTS) or speech-recognition (ASR) claim.
- **Connectivity:** OSIPTEL reports guaranteed 4G in about 77% of populated areas nationally (Q3 2025) [V]. Rural Altiplano coverage must be checked per community. Community radio is the likely complement to WhatsApp.

## Preconditions for a real Puno pilot

1. **Observations.**
   - *Option A:* shielded Tmin loggers on about 5 parcels per community for one frost season (about US$30–60 each).
   - *Option B:* a SENAMHI data agreement. Registration is required and is reported to ask for a Peruvian DNI [V], so it probably needs a Peruvian partner.
   - Either option turns parcels from forecast-only into station-anchored, which is the regime that measured 3.0 → 1.4 °C in Toluca.
2. **Agronomic sign-off.** An INIA Puno agronomist or Agencia Agraria must sign the crop thresholds and in-field windows.
3. **Program rules.** SAC rules must be read from the current norm, and a MIDAGRI or Agencia Agraria contact must agree to receive packets.
4. **Voice.** Quechua and Aymara voice notes must be recorded and comprehension-tested with farmers.
5. **Retraining.** Retrain every season, because the forecast-minus-reference bias moved by 1 °C between 2024 and 2025 even against reanalysis.
