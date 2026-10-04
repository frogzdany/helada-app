# helada_model

Parcel-level night minimum temperature (Tmin) and calibrated frost probability for rainfed maize in the Toluca, Ixtlahuaca and Atlacomulco valleys (Estado de México). This is the `helada_model` package; the app (`../app`) calls it through one adapter.

```python
import helada_model as hm
p = hm.Parcel("p1", 19.4622, -99.7756)                 # elev_m is filled from the DEM if None
fc = {"tmin_c": 2.5, "dew_c": -6, "cloud_pct": 5, "wind_kmh": 4, "tmax_prev_c": 21}
[f] = hm.predict([p], "2026-01-10", forecast=fc)        # -> [FrostForecast(...)], offline, ~1.5 ms per parcel
hm.support_of(f)                                        # "station-anchored" | "terrain-transfer" (also accepts a Parcel)
hm.climatology(p)                                       # frost-date percentiles for planting advice
hm.backtest_report()                                    # evidence numbers, both regimes
hm.predict([p], "2025-11-14", forecast=fc, variant="holdout_2025_26")   # model trained WITHOUT the 2025-26 season
sc = hm.calibrate_site(p, [("2025-11-15", -1.5), ...], date_is="morning")   # a parcel logger's nightly minima
hm.predict([p], "2026-01-10", forecast=fc, sites=[sc])   # support "logger-anchored (k noches)" if p is off-station
hm.expected_precision(30)                               # measured error for a site with 30 logger nights
```

## Summary

- **Near an SMN station, the model is strong.** At stations it has seen, on an unseen season, Tmin MAE drops from 2.65 °C (Open-Meteo default forecast) to 1.40 °C, and frost-night recall rises from 24% to 58% at a 5% false-alarm rate.
- **At sites with no station, the gain is small.** At held-out stations, MAE drops from 2.83 to 2.32 °C, which is mostly a bias correction. Frost detection is about the same as the default forecast: 32% vs 32% recall at 5% POFD, and +5 pp at 10% POFD.
- **Terrain features do not close the unseen-site gap.** We tried twelve Copernicus-DEM features and five transfer methods. The best transfer method recovers about 10% of the gap between known and unseen sites (0.10 °C out of 1.03 °C).
  - Station microclimate offsets are nearly spatially random. The semivariance of offsets between stations 0–5 km apart is already 83% of the total variance. Terrain explains little of them: |Spearman ρ| ≤ 0.3.
- **The product therefore has two honest regimes.** `drivers["support_station_anchored"]` says which regime a parcel is in. The terrain-transfer regime carries wider intervals, calibrated on held-out stations.
- **A parcel logger closes most of the gap, and we measured how fast** (section "Logger learning curve"). On held-out stations, 30 nights of readings cut Tmin MAE from 2.32 to 1.79 °C and 60 nights to 1.56 °C, about the same as a station the model was trained on (1.55 on the same nights). Frost detection improved reliably only with a full season of readings. `calibrate_site()` adds this as a third regime, "logger-anchored (k noches)".

## Interface notes (for the app)

- **`date` is the local *evening* date.** The night of `2026-01-10` runs from 18:00 on Jan 10 to 08:00 on Jan 11. SMN's 08:00 reading dated Jan 11 is the observation that verifies it.
- **`forecast` has three accepted forms:**
  - one scalar dict for all parcels;
  - `{parcel_id: dict}`;
  - `None`, which fetches from Open-Meteo and needs a network connection.
- **Scalar dict keys (aliases in brackets):**
  - `tmin_c` [`grid_tmin_c`], required: the minimum of the hourly 2 m temperature from 18:00 to 08:00.
  - `dew_c` (night-mean dew point) or `rh` [`rh_pct`] (night-mean RH %). RH is converted with Magnus at tmin + 3 °C.
  - `cloud_pct` [`cloud_cover`], night mean.
  - `wind_kmh`, or `wind_ms` in m/s, night mean.
  - `tmax_prev_c`: the maximum between 12:00 and 17:00 on the evening date.
  - `grid_elev_m`, optional: the elevation the forecast temperature refers to.
    - When it is given and differs from the parcel's DEM elevation, `tmin_c`/`tmax_prev_c` are moved to the parcel elevation at 6.5 °C/km. This is what Open-Meteo's own point downscaling does, and the training data used it.
    - The shift is reported in `drivers["lapse_adj_c"]`.
    - Pass it when you fetch with `elevation=nan` (the raw grid cell). Leave it out or pass `None` for a normal point request.
  - Extra keys (for example `source`) are ignored.
  - If an optional key is missing, a neutral default is used. `drivers["forecast_fallback"]` counts these, and each one widens the interval by 0.5 °C per side.
  - Example, as the app's demo scenario (a replay of the real night 2025-11-14) passes it: `{"P01": {"grid_tmin_c": 9.2, "dew_c": 0.29, "cloud_cover": 10.7, "wind_kmh": 2.73, "tmax_prev_c": 21.5, "grid_elev_m": 2673.0}}`
- **Forecast source.** The model was trained on the Open-Meteo **`ecmwf_ifs025`** day-1 forecast at the point (default elevation downscaling). For live use, fetch with `models=ecmwf_ifs025`, or pass `forecast=None` and let `helada_model.forecast.fetch_night()` do it.
  - Feeding the `best_match` default blend works, but it is off-distribution. On the 2025-26 test nights it averaged about 0.7 °C colder than `ecmwf_ifs025`, so parcel Tmin would come out about that much too cold. That errs toward extra alerts.
- **`support_of(x)`** accepts a `FrostForecast` or a `Parcel`. A forecast made with a logger calibration returns `"logger-anchored (k noches)"`.
- **`calibrate_site(parcel, observations, forecasts=None, variant=None, date_is="evening") -> SiteCalibration`** turns a parcel's own nightly minima (`[(date, tmin_c), ...]`) into a site correction. See "Logger learning curve".
  - It needs the archived day-1 forecast of each logged night. Pass `forecasts={evening_date: scalar dict}`, or leave `None` to fetch them in one call from the Open-Meteo Previous Runs API (`forecast.fetch_archive`). Nights without a forecast, readings outside −25…30 °C and unreadable dates are listed in `SiteCalibration.rejected`, never guessed.
  - `date_is="morning"` is for readings dated by the morning the thermometer was read (the SMN convention); they are stored as the evening before.
  - `SiteCalibration` is a dataclass with `to_dict()`/`from_dict()` (JSON-safe). It keeps the nights and their forecasts, so it can be refitted for another variant or cut-off.
- **`predict(..., sites={parcel_id: SiteCalibration})`** (or a list). For a terrain-transfer parcel it uses only logger nights **strictly before** the forecast night, so replays never leak. It adds the empirical-Bayes correction and uses the interval calibration measured for that many nights. Station-anchored parcels keep the station regime, which was validated as better. New drivers: `support_logger_anchored`, `logger_nights`, `logger_offset_c`, `logger_expected_mae_c`.
- **`variant`** (optional keyword of `predict`): `None` = the shipped model (trained on all data through Sep 2026). `"holdout_2025_26"` = the same product trained only on nights before 2025-10-01, with calibration from the 2024-25 out-of-sample residuals (`artifacts/holdout_2025_26/`, 370 KB gzipped models, built by `research/09_holdout_artifact.py`).
  - It reproduces the backtest's known-station predictions exactly. Use it to replay any 2025-26 night without in-sample leakage; the app's demo replay does.
  - `drivers["variant_holdout_2025_26"]` is 1.0 when it answered.
- **`drivers` (all floats):**
  - `support_station_anchored` (1/0), `nearest_station_id`, `nearest_station_km`, `station_offset_c`
  - `elev_m`, `dem_elev_m`, `elev_diff_m` (parcel minus the mean elevation within 10 km, which is about the NWP grid-cell scale)
  - `tpi` (1 km), `tpi_3k`, `height_above_valley_floor_m`, `slope_deg`, `northness`, `horizon_deg`
  - `clear_sky`, `wind_kmh`, `dewpoint_depression_c`, `clear_calm`
  - `correction_c` (model minus grid), `lapse_adj_c`, `forecast_fallback`
- **Anchoring rule:** a parcel is station-anchored if it lies within 1.5 km of a training station and its DEM elevation is within 50 m of the station's.
  - Anchored parcels use the station's own site descriptors and learned offset, which is exactly the validated setting.
  - All other parcels use terrain-transfer.
- **Domain:** lat 18.9–20.1, lon −100.3 to −99.2. Outside it, `predict` raises `ValueError`.
- **Alert rule: ALERT if `p_frost ≥ 0.3`.** Backtested, it fires on about 7 of every 100 station-nights, with 49% recall at 3.5% POFD (anchored) and 30% recall at 4.6% POFD (transfer).
  - Terrain-transfer parcels with `tmin_lo_c ≤ 0` and `p_frost < 0.3` get a text-only WATCH, not an alert.
  - The old rule (`… or tmin_lo_c ≤ 0`) fired on 25–29 of every 100 station-nights (88% / 71% recall). `backtest.json` still reports it under `contract_alert_rule`.
  - `p_frost ≥ 0.2` gives 73% (anchored) / 51% (transfer) recall at 9% / 11% POFD, with about 15 alerts per 100 nights. See `backtest_report()["…"]["alternative_rules"]`.

## Method

**Target.** The observed night Tmin from CONAGUA/SMN, a manual 08:00 reading at shelter height. Frost means Tmin ≤ 0 °C.

**Inputs.** The day-1 forecast (the run from about 24 h before), aggregated over the night:
- Tmin; mean dew point, cloud and wind; the previous afternoon's maximum.
- Derived: diurnal range, dew-point depression, day-of-year harmonics, and a clear-calm index `(1 − cloud/100)·exp(−wind/8)`.

**Terrain.** Twelve point features are computed by `terrain.py` from a Copernicus GLO-30 DEM, block-averaged to 6″ (about 180 m). The same code runs at training and at inference, so they match exactly.
- elevation
- TPI at 300 m, 1 km and 3 km (cold-air-pooling proxies)
- elevation minus the 10 km mean (grid-cell scale)
- height above valley floor (elevation minus the 5th percentile within 5 km)
- 5 km relief
- elevation rank within 1 km and 3 km
- slope, northness
- mean horizon angle over 16 azimuths out to 3 km (a sky-view proxy)

A 1″ (30 m) version was also tested and did not help.

**Product models.** LightGBM, 300 trees, 15 leaves, L1 loss. All artifacts are gzipped, and the models total 372 KB.

| Regime | Model | Validated how |
|---|---|---|
| station-anchored | GBM(weather + 12 terrain) + that station's learned offset `a + b·clear_calm` (ridge-shrunk) | forward holdout: train Feb 2024–Sep 2025, test Oct 2025–Mar 2026 |
| terrain-transfer | mean of GBM(weather + elev/lat/lon) and a regularised GBM(weather + 6 terrain; every leaf ≥ 3000 nights, i.e. several stations) | 10-fold leave-stations-out, test windows Oct–Mar 2024-25 and 2025-26; training excludes the test window, so there is no same-night leakage |

**Calibration.**
- `p_frost` and the 80% interval come from the empirical out-of-sample residual distribution of each regime.
  - Residuals are binned by clear-calm tercile × predicted-Tmin bin.
  - A binned isotonic map is applied on top.
- Because the transfer residuals come from held-out stations, the transfer intervals are automatically wider (mean width 7.0 °C vs 5.5 °C).
- Reliability is checked **cross-season**: fit on one Oct–Mar window, test on the other. This means held-out season *and* held-out stations.

**Climatology.** Frost dates come from 1991–2025 SMN daily records, 103 stations with at least 10 good years.
- Anchored parcels use their station's record.
- Other parcels use up to 5 stations within 25 km (IDW). Each station's record is read at the threshold τ = −δ, where δ is the modelled parcel-minus-station difference over 64 reference cold nights.
- `last_spring_frost_doy_p90` means that in 9 of 10 years the last frost fell before this day. `first_autumn_frost_doy_p10` means that in 1 of 10 years the first frost fell before this day.

## Results

All numbers are station-nights in the frost season (Oct–Mar). "Recall @POFD x%" means each method's alert threshold is set so that x% of frost-free station-nights get an alert: an ROC-matched comparison that is equally fair to the raw forecast. The 95% CIs come from a bootstrap that resamples whole nights (500 samples; 300 for the "vs default" comparison).

### Product backtest (`backtest_report()`)

| Regime | n | Frost | Stations | Seasons | Tmin MAE | ΔMAE [95% CI] | Recall @5% POFD | Δ pp [CI] | Recall @10% POFD | Δ pp [CI] | Brier (clim.) | 80% PI coverage |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Known station** vs raw ECMWF | 5,855 | 503 | 39 | 2025-26 | 3.00 → **1.40** | 1.60 [1.50, 1.73] | 24.7% → **57.9%** | +33 [27, 44] | 36.8% → **76.9%** | +40 [32, 50] | 0.054 (0.079) | 0.87 |
| **Known station** vs Open-Meteo default | 5,855 | 503 | 39 | 2025-26 | 2.65 → **1.40** | 1.25 [1.16, 1.35] | 24.3% → **57.9%** | +34 [26, 43] | 40.0% → **76.9%** | +37 [30, 45] | | |
| **Unseen site** vs raw ECMWF | 19,592 | 1,977 | 80 | 2024-25 + 2025-26 | 3.13 → 2.32 | 0.81 [0.75, 0.87] | 23.4% → 32.4% | +9 [5, 13] | 38.6% → 48.2% | +10 [6, 13] | 0.078 (0.091) | 0.76 |
| **Unseen site** vs Open-Meteo default | 19,592 | 1,977 | 80 | 2024-25 + 2025-26 | 2.83 → 2.32 | 0.50 [0.45, 0.56] | 32.1% → 32.4% | +0 [−2, 5] | 43.1% → 48.2% | +5 [2, 9] | | |

**Reliability, cross-season** (mean predicted p / observed frequency, with n):

| Regime | 0–0.05 | 0.05–0.15 | 0.15–0.30 | 0.30–0.50 | 0.50–0.70 | 0.70–1 |
|---|---|---|---|---|---|---|
| Known station | 0.009 / 0.007 (3473) | 0.10 / 0.04 (938) | 0.20 / 0.19 (1013) | 0.37 / 0.48 (259) | 0.60 / 0.66 (116) | 0.78 / 0.84 (56) |
| Unseen site | 0.012 / 0.018 (7192) | 0.09 / 0.08 (8187) | 0.21 / 0.22 (2807) | 0.39 / 0.39 (1278) | 0.56 / 0.67 (127) | 0.70 / 1.0 (1) |

**Unseen-site product, per fold** (MAE raw ECMWF → model):

| Fold | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| Stations | 9 | 9 | 8 | 8 | 7 | 8 | 7 | 8 | 8 | 8 |
| MAE | 2.80→1.90 | 3.34→1.79 | 3.79→2.35 | 2.58→2.16 | 2.62→2.52 | 3.09→2.50 | 3.22→2.81 | 2.61→1.91 | 3.33→2.48 | 3.99→2.83 |

By season: 2024-25 (80 stations) 3.19 → 2.41; 2025-26 (39 stations) 3.00 → 2.12.

**Strict-forward check** (2025-26, train strictly before Oct 2025, baseline GBM): ΔMAE 0.78 [0.69, 0.88]. The 2025-26 unseen-site numbers are not an artifact of training on later nights.

### Logger learning curve (`research/10_logger_learning_curve.py`)

**Question.** How many nights of a parcel logger does a new site need before its forecast reaches the station-anchored regime?

**Simulation.** Each SMN station in the unseen-site backtest stands in for a new logger. This needs no new data and has no leakage by construction:
- **Base prediction:** the terrain-transfer product's 10-fold leave-stations-out prediction. The station was never in the training set of the model that made it.
- **Revealed nights:** the logger "reveals" the station's first k observed nights of a frost season (k = 0, 7, 14, 30, 60, 90), or the whole previous season (2024-25 revealed, 2025-26 evaluated).
- **Site correction:** r = a + b·clear_calm (r = observed − base), from an empirical-Bayes posterior.
  - Prior N(0, Σ) centred on the terrain-transfer prediction, so with k = 0 the prediction *is* terrain-transfer.
  - Night noise σ² inflated by (1+ρ)/(1−ρ) for night-to-night autocorrelation.
  - Σ, σ² and ρ come only from **other** stations (other CV folds) in the **other** season.
  - Fitted values (all data): SD of the offset a 1.8 °C, σ = 1.9 °C, ρ = 0.56.
- **Evaluation:** strictly **after** the revealed nights, on one common set: the nights after the 90th observed night of the season (≈ Jan–Mar, the core frost months). The set is identical for every k, so the curve is not confounded by season composition. It covers 110 station-seasons, 79 stations, 9,092 station-nights and 852 frost nights.
  - Recall uses ROC-matched thresholds, as elsewhere in this README.
  - The 80% interval calibration is cross-fitted by season.
  - The ceiling is the station-anchored product's out-of-sample prediction on the same station-nights.
  - CIs: 500 bootstrap resamples of whole nights. "Δ recall" is paired against k = 0. "Improved" is the share of station-seasons whose MAE fell against k = 0.

**Both test seasons pooled** (2024-25 + 2025-26):

| Logger nights | Tmin MAE [95% CI] | Recall @5% POFD (Δ pp vs k=0 [CI]) | Recall @10% POFD (Δ [CI]) | 80% PI coverage / width | Improved |
|---|---|---|---|---|---|
| 0 (unseen-site product) | 2.32 [2.28, 2.37] | 29.7% | 44.6% | 0.77 / 7.1 °C | – |
| 7 | 2.12 [2.07, 2.18] | 33.8% [+0, +8] | 50.1% [+1, +9] | 0.76 / 6.5 °C | 51% |
| 14 | 1.94 [1.89, 1.98] | 37.6% [+3, +14] | 56.1% [+7, +15] | 0.78 / 6.3 °C | 59% |
| 30 | 1.79 [1.75, 1.84] | 38.0% [+3, +14] | 57.3% [+8, +17] | 0.80 / 5.8 °C | 57% |
| 60 | **1.56** [1.52, 1.60] | 48.6% [+15, +24] | 63.7% [+15, +23] | 0.80 / 4.9 °C | 67% |
| 90 | **1.53** [1.50, 1.57] | 51.5% [+17, +26] | 64.2% [+16, +23] | 0.75 / 4.6 °C | 70% |
| *Station-anchored ceiling* | *1.55* [1.50, 1.59] | *46.7%* [+12, +21] | *61.3%* [+12, +20] | *0.76 / 4.6 °C* | *80%* |

The station-cluster bootstrap agrees. Δ MAE against k = 0, resampling station-seasons: k = 7 [+0.00, +0.39], k = 30 [+0.27, +0.78], k = 90 [+0.56, +1.04]. MAE on *all* nights after k, not just the common set, gives the same curve: 2.34 → 2.12 (k = 7), 2.38 → 1.87 (k = 30), 2.35 → 1.62 (k = 60).

**But the two seasons disagree on frost detection.**

2024-25 (76 stations, 591 frost nights). MAE 2.36 → 1.85 (k = 14) → 1.56 (k = 60). Recall @5% 27% → 49% (k = 14) → 61% (k = 90). This is above the ceiling of 42%, which in this window used offsets from other seasons.

2025-26, the 34 stations that also had a 2024-25 season (261 frost nights):

| Logger nights | Tmin MAE [CI] | Recall @5% (Δ [CI]) | Recall @10% (Δ [CI]) | Coverage / width |
|---|---|---|---|---|
| 0 | 2.21 [2.12, 2.31] | 33.7% | 47.9% | 0.83 / 7.8 °C |
| 14 | 2.16 [2.07, 2.27] | 18.4% [−22, −9] | 35.2% [−18, −6] | 0.74 / 6.0 °C |
| 30 | 1.89 [1.80, 1.99] | 14.6% [−25, −14] | 34.9% [−19, −5] | 0.79 / 5.9 °C |
| 60 | 1.57 [1.50, 1.67] | 20.7% [−21, −8] | 44.8% [−8, +7] | 0.80 / 5.2 °C |
| 90 | 1.50 [1.41, 1.60] | 23.4% [−17, −2] | 53.3% [+0, +13] | 0.78 / 4.8 °C |
| **Previous full season (2024-25, median 182 nights)** | **1.54** [1.45, 1.63] | **44.8%** [+5, +18] | **67.8%** [+12, +27] | 0.76 / 4.8 °C |
| Previous season + first 90 nights of this one | 1.47 [1.38, 1.57] | 44.4% [+5, +18] | 67.4% [+13, +27] | 0.78 / 4.8 °C |
| *Station-anchored ceiling* | *1.40* [1.32, 1.49] | *55.6%* [+14, +30] | *75.5%* [+22, +35] | *0.87 / 5.5 °C* |

**What it means.**
- **The error curve is consistent.** Every k ≥ 14 lowers MAE in both seasons, and by 60 nights (about two months) the error equals the station-anchored level.
- **Frost detection within the first season is not reliable.** In 2025-26, offsets learned from Oct–Dec nights *lowered* recall on Jan–Mar nights.
  - The site offsets are stable overall (early vs late correlation 0.87–0.93). But the three frost-heavy stations were 1.3–2.4 °C colder than their Oct–Dec offset said.
  - Meanwhile, cold-offset stations without frost pushed the matched-POFD threshold down.
- **A full previous season was the setting that improved recall in the season we could test** (+11 pp at 5% POFD, +20 pp at 10%). It is still short of a real station (−11 pp).
- **What can be claimed:** "About two months of readings bring a new parcel's Tmin error down to the station-anchored level (2.3 → 1.6 °C); better frost detection needs a full season of readings."

**Shrinkage matters only for small k.** With 7 nights, a naive mean offset gives MAE 2.20 and recall +1 pp. The empirical-Bayes offset gives 2.11 and +4 pp. From 30 nights on the estimators agree within 0.06 °C. The clear-calm slope adds 0.03–0.06 °C at k = 14–30.

**Product (`artifacts/logger_curve.json`, 67 KB).** It stores the EB hyperparameters and, for each level k ∈ {0, 7, 14, 30, 60, 90, 182}:
- the measured MAE, recall and interval width;
- a `calib.fit` interval/probability calibration fitted on that level's out-of-sample residuals.

A site with k nights uses the largest level ≤ k, which is conservative. The app keeps the terrain-transfer WATCH safety net for logger parcels until 150 nights.

**Real-site illustration (not evidence).** SMN 15390 E.T.A. 013 Jocotitlán is in neither station table, so its coordinates are terrain-transfer. Using its 2025-26 readings as the logger (`app/scripts/build_demo_logger.py`, holdout model):
- 44 nights (Oct 1 – Nov 13) cut MAE on the remaining 138 nights of the season from 1.94 to 1.34 °C.
- On the replay night 2025-11-14 the band went from +2.0 [−2.1, +6.9] °C, P = 41% (an alert), to +3.1 [−0.5, +5.4] °C, P = 17%. The station measured +2.0 °C.

**Limits.**
- It is simulated with SMN shelter readings: a real logger has its own siting and sensor bias. A *consistent* bias is exactly what the offset learns. A moved or unshielded sensor is not.
- Two frost seasons, 2025-26 frost concentrated at three stations.
- Evaluation is on Jan–Mar nights only.
- Coverage at k = 90 is 0.75, below the nominal 0.80.

### Research: can terrain close the unseen-site gap? (`research/05_experiments.py`, `05b_variants.py`)

**Leave-stations-out, pooled 2024-25 + 2025-26, forecast `ecmwf_ifs025`** (80 stations, 19,592 station-nights, 1,977 frost):

| Method | MAE | Recall @5% | Recall @10% | AUC |
|---|---|---|---|---|
| Raw day-1 forecast | 3.13 | 23.4% | 38.6% | 0.745 |
| Global bias removal | 2.59 | 23.1% | 38.1% | 0.742 |
| GBM weather + lat/lon/alt (baseline) | 2.43 | 32.2% | 44.7% | 0.795 |
| GBM weather + 12 terrain + lat/lon | 2.63 | 19.3% | 26.9% | 0.629 |
| GBM weather + 12 terrain | 2.53 | 18.9% | 27.5% | 0.622 |
| Ridge, terrain × clear-calm interactions | 2.54 | 21.1% | 35.1% | 0.728 |
| Weather-only GBM (no site info) | 2.54 | 13.6% | 27.5% | 0.706 |
| … + nearest station's offset | 2.99 | 9.9% | 21.9% | 0.660 |
| … + IDW of neighbour offsets | 2.61 | 15.9% | 29.6% | 0.709 |
| … + kriging (GP) of offsets | 2.54 | 15.0% | 28.5% | 0.710 |
| … + terrain regression of offsets | 2.52 | 18.7% | 32.4% | 0.729 |
| … + regression-kriging of offsets | 2.52 | 19.2% | 32.2% | 0.728 |
| Regularised terrain GBM (leaf ≥ 3000 nights) | 2.41 | 22.8% | 34.6% | 0.752 |
| **Ensemble: baseline GBM + regularised terrain GBM (product)** | **2.33** | **33.4%** | **44.3%** | **0.806** |
| *Reference: known-station anchored model* | *1.40* | *60.0%* | *75.7%* | *0.920* |

**Same study with Open-Meteo `best_match`** (87 stations, 21,048 station-nights, 2,148 frost):

| Method | MAE | Recall @5% | Recall @10% |
|---|---|---|---|
| Raw | 2.82 | 31.0% | 43.9% |
| Baseline GBM | 2.48 | 33.4% | 44.4% |
| Regression-kriging | 2.45 | 24.5% | 35.0% |
| Ensemble | 2.36 | 33.8% | 48.9% |
| *Known-station anchored* | *1.57* | *53.0%* | *71.0%* |

The conclusion is the same.

**Why terrain fails here:**

1. **The station offsets are almost pure nugget.** The offset is the mean of observed minus forecast Tmin; across stations it has an SD of 2.3 °C.
   - Semivariance: 4.6 (0–5 km), 5.0 (5–10 km), 4.6 (10–20 km), against a total variance of 5.5.
   - Borrowing the nearest station's correction (median distance 6 km) makes MAE *worse* than doing nothing.
2. **Terrain correlates only weakly with the offsets** (Spearman): slope 0.29, height above valley floor 0.22, elevation 0.19, TPI 3 km 0.12, TPI 300 m −0.03. A GBM fed rich terrain features memorises station identity and overfits (MAE 2.63).
3. **The offsets are mostly not a radiative signal.** On cloudy, windy nights they still have an SD of 2.2 °C, and cloudy-night offsets correlate 0.91 with clear-night offsets. Cold-air pooling cannot explain that.
   - So a large share is station-specific siting, exposure, instrument or reading practice.
   - A 30 m or 180 m DEM cannot see these, and a farmer's parcel does not share them.
4. **Distance matters only very close in.** For the 6 held-out stations within 3 km of a training station, the nearest station's offset did help (MAE 2.11 vs 2.22). Beyond 3 km it hurt: 2.73–3.67 vs 2.34–2.72. This is why anchoring is limited to 1.5 km.

## Data sources

| Data | Source | Notes |
|---|---|---|
| Observed daily Tmin | CONAGUA/SMN daily climatology, `https://smn.conagua.gob.mx/tools/RESOURCES/Normales_Climatologicas/Diarios/mex/dia{ID}.txt` | 186 stations in lat 18.9–20.1, lon −100.3 to −99.2, altitude ≥ 2300 m. QC: range check, Tmin ≤ Tmax, a station-month MAD outlier cut, stuck-value runs of 7 or more. Records run 1946–Sep 2026. |
| Archived day-1 forecasts | Open-Meteo Previous Runs API, `https://previous-runs-api.open-meteo.com/v1/forecast` (docs: https://open-meteo.com/en/docs/previous-runs-api), CC BY 4.0 | `ecmwf_ifs025` from Mar 2024 (83 of 91 stations; the rest hit the hourly rate limit) and `best_match` from Feb 2024 (91 stations). Queried at each station's coordinates. |
| Live forecast (optional) | Open-Meteo Forecast API, `https://api.open-meteo.com/v1/forecast?models=ecmwf_ifs025` | Only used when `forecast=None`. |
| DEM | Copernicus DEM GLO-30 (© DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the EU and ESA), AWS Open Data COGs `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N19_00_W100_00_DEM/…` (registry: https://registry.opendata.aws/copernicus-dem/) | 6 tiles (N18–N20, W100–W101). Shipped as `artifacts/dem_6s.npz` (0.87 MB, int16). INEGI CEM 3.0 (15 m, https://www.inegi.org.mx/app/geo2/elevacionesmex/) needs an interactive download, so we did not use it. GLO-30 is a surface model (DSM), so buildings and trees are included. |

## Artifacts (`src/helada_model/artifacts/`, 1.36 MB total)

| File | Size | Content |
|---|---|---|
| `model_anchor.txt.gz` | 154 KB | station-anchored GBM |
| `model_transfer_wx.txt.gz`, `model_transfer_terrain.txt.gz` | 147 + 71 KB | transfer ensemble |
| `model_meta.json` | 53 KB | features, the station table with learned offsets, calibration tables, reference nights |
| `climatology.json` | 72 KB | frost-date percentiles per station at τ = −3…+3 °C |
| `backtest.json` | 12 KB | evidence numbers |
| `logger_curve.json` | 67 KB | logger learning curve: EB hyperparameters, per-k interval calibration and measured errors |
| `dem_6s.npz` | 865 KB | terrain for any parcel, offline |

**Model artifact: 372 KB (< 1 MB).** The whole package runs offline and fits on a Raspberry Pi.

## Reproduce

```sh
cd model && uv sync
uv run python research/01_fetch_smn.py                 # SMN download + QC  -> ../data/smn
uv run python research/02_fetch_forecasts.py           # Open-Meteo archive -> ../data/forecasts (rate-limited; resumable)
uv run python research/03_dem.py <dir with 6 GLO-30 tiles>   # -> ../data/dem, ../data/terrain_stations.csv
uv run python research/04_dataset.py                   # station-night tables
uv run python research/05_experiments.py best_match ecmwf_ifs025 && uv run python research/05b_variants.py ecmwf_ifs025
uv run python research/06_climatology.py               # -> artifacts/climatology.json
uv run python research/07_train_product.py ecmwf_ifs025 # -> artifacts/*, backtest.json
uv run python research/08_tables.py                    # markdown tables
uv run python research/09_holdout_artifact.py ecmwf_ifs025 # -> artifacts/holdout_2025_26/ (trained without 2025-26)
uv run python research/10_logger_learning_curve.py ecmwf_ifs025 # -> artifacts/logger_curve.json, research/out/logger_curve_*.json
uv run pytest -q                                       # model tests (interface, regimes, calibration, climatology, offline) + tests/test_logger.py
```

## Limitations

- **The ground truth is SMN manual shelter readings.** Part of what we call "microclimate" is station siting and instrument error. A crop canopy on a radiative night is typically 1–2 °C colder than a shelter at 1.5 m, so shelter Tmin ≤ 0 °C is a conservative frost definition for maize leaves.
- **The forecast archive is short.** All-variable archives start in Feb/Mar 2024, so there are two frost seasons for testing. The known-station test is one season (2025-26, 39 stations, 503 frost station-nights). Frost nights cluster at a handful of valley-floor stations.
- **Training uses both test windows.** Unseen-site results pool 2024-25 and 2025-26. For 2024-25, training included nights after the window, but never the same nights. The strict-forward 2025-26 check agrees.
- **SMN coordinates are imprecise.** For 15 of 184 stations, the metadata altitude disagrees with the DEM by more than 100 m, so terrain at the station may be computed at the wrong spot. This biases the terrain analysis toward "no signal", but by an unknown amount.
- **Station-anchored accuracy is validated only at the station itself.** Applying it within 1.5 km is an assumption. The only evidence is that 6 station pairs under 3 km apart shared offsets somewhat.
- **Seasonal drift.** The blends and bias shift between seasons: the `best_match` warm bias went from +3.4 °C in 2021-22 to +1.5 °C in 2025-26. The model should be retrained every season.
- **Climatology transfer to off-station parcels is modelled, not validated.** It inherits the weak unseen-site skill.

## What would improve unseen-site accuracy

This is also a product precondition.

1. **Low-cost Tmin loggers on about 5 pilot parcels per ejido.** Now measured and built: see "Logger learning curve" and `calibrate_site()`.
   - About two months of nightly readings bring Tmin MAE from 2.32 to 1.56 °C, the station-anchored level. Reliable gains in frost detection needed a full season.
   - Hardware: a min-max thermometer read by the farmer each morning and sent by WhatsApp (≈ MX$160–210 [V]), or an IP65 Bluetooth logger with a DIY radiation shield (≈ MX$670). For 5 per ejido that is ≈ MX$1,000 or ≈ MX$3,300. Prices and sources: `../docs/tradeoffs-preconditions.md` §7.
   - This is the single biggest lever. A logger can also measure *crop-height* temperature rather than shelter temperature. If it does, its offset absorbs the difference.
2. **Farmer frost reports** (a WhatsApp "¿heló anoche?" yes/no) as weak labels, to learn per-parcel offsets over time.
3. **Add more distinct sites.** The ~50 Edomex stations with pre-2024 records only could be used with ERA5-Land or reforecast inputs, together with a station-quality screen (for example, flagging stations whose cloudy-night offset is large, which indicates a siting or instrument problem).
4. **Pin the forecast source** (done: `ecmwf_ifs025`) and retrain on a rolling window each September, before the early-frost risk.
