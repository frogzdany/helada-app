"""Puno country-swap evidence: day-1 ECMWF forecast vs ERA5 reanalysis at 10 Altiplano points.

READ THIS FIRST: there is no station data here (SENAMHI downloads need a DNI registration), so the reference is ERA5,
a reanalysis produced by an older version of the same ECMWF model at ~31 km. Agreement with ERA5 is NOT forecast
skill at a parcel. It is used only to (1) size a lower bound on the band, (2) show the forecast is not wildly off the
reanalysis, and (3) show how often reanalysis nights cross the potato thresholds. Everything is labelled so.

Sources (all Open-Meteo, CC BY 4.0, no key):
  forecast   previous-runs-api .../v1/forecast  models=ecmwf_ifs025, *_previous_day1 (the run ~24 h before; the
             same source and lead the Toluca model was trained on)
  forecast0  historical-forecast-api .../v1/forecast  models=ecmwf_ifs025 (stitched first hours, lead ~0; context only)
  era5       archive-api .../v1/archive  models=era5 (0.25 deg)          <- reference
  era5_land  archive-api .../v1/archive  models=era5_land (0.1 deg)      <- reference-uncertainty check

Night = 18:00 -> 08:00 America/Lima, keyed by the EVENING date (same convention as helada_model).

Run:   cd model && uv run python regions/puno_eval.py          (network; raw hourly cached in regions/cache/)
Out:   regions/out/puno_nights.csv.gz   one row per point-night (used by the demo runner offline)
       regions/out/puno_eval.json       measured numbers + the band quantiles the runner uses
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from helada_model.features import nights_from_hourly  # noqa: E402
from helada_model.regions import load  # noqa: E402

CACHE = os.path.join(HERE, "cache"); OUT = os.path.join(HERE, "out")
START, END = "2024-03-01", "2026-09-20"
TZ = "America/Lima"
VARS = ["temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m"]
SOURCES = {
    "fc": ("https://previous-runs-api.open-meteo.com/v1/forecast", "ecmwf_ifs025", [f"{v}_previous_day1" for v in VARS]),
    "fc0": ("https://historical-forecast-api.open-meteo.com/v1/forecast", "ecmwf_ifs025", ["temperature_2m"]),
    "era5": ("https://archive-api.open-meteo.com/v1/archive", "era5", ["temperature_2m"]),
    "era5_land": ("https://archive-api.open-meteo.com/v1/archive", "era5_land", ["temperature_2m"]),
}
THRESHOLDS = [0.0, -1.0, -2.5, -3.5, -5.0]
SEASONS = {  # evening-date windows
    "cold_2024": ("2024-05-01", "2024-08-31"), "cold_2025": ("2025-05-01", "2025-08-31"),
    "crop_2024_25": ("2024-10-15", "2025-04-30"), "crop_2025_26": ("2025-10-15", "2026-04-30"),
    "cold_2026": ("2026-05-01", "2026-08-31"),
}
SEASON_TYPES = {"cold": ["cold_2024", "cold_2025"], "crop": ["crop_2024_25", "crop_2025_26"]}
SITE_SD_MC_DRAWS = 40


def fetch(src: str, pid: str, lat: float, lon: float) -> pd.DataFrame:
    fp = os.path.join(CACHE, src, f"{pid}.csv.gz")
    if os.path.exists(fp):
        return pd.read_csv(fp)
    url, model, hv = SOURCES[src]
    p = dict(latitude=lat, longitude=lon, start_date=START, end_date=END, timezone=TZ, models=model, hourly=",".join(hv))
    for a in range(6):
        try:
            r = requests.get(url, params=p, timeout=180)
            if r.ok:
                break
            print(src, pid, r.status_code, r.text[:200], flush=True)
        except Exception as e:  # noqa: BLE001
            print(src, pid, e, flush=True)
        time.sleep(20 * (a + 1))
    else:
        raise RuntimeError(f"could not fetch {src} for {pid}")
    j = r.json(); df = pd.DataFrame(j["hourly"])
    df.columns = [c.replace("_previous_day1", "").replace(f"_{model}", "") for c in df.columns]
    df["elev"] = j.get("elevation"); df["grid_lat"] = j.get("latitude"); df["grid_lon"] = j.get("longitude")
    os.makedirs(os.path.dirname(fp), exist_ok=True); df.to_csv(fp, index=False)
    print("fetched", src, pid, len(df), flush=True); time.sleep(3)
    return df


def night_tmin(h: pd.DataFrame) -> pd.Series:
    h = h[["time", "temperature_2m"]].copy()
    for c in ("dew_point_2m", "cloud_cover", "wind_speed_10m"):
        h[c] = 0.0
    n = nights_from_hourly(h)
    return pd.Series(n.fc_tmin.values, index=n.date - pd.Timedelta(days=1))


def build_nights(region) -> pd.DataFrame:
    rows = []
    for p in region.config["parcels"]:
        pid, lat, lon = p["id"], p["lat"], p["lon"]
        h = fetch("fc", pid, lat, lon)
        n = nights_from_hourly(h[["time"] + VARS])
        n["evening"] = n.date - pd.Timedelta(days=1)
        d = n.set_index("evening")[["fc_tmin", "fc_dew", "fc_cloud", "fc_wind", "fc_tmax_prev"]]
        for src in ("fc0", "era5", "era5_land"):
            d[f"{src}_tmin"] = night_tmin(fetch(src, pid, lat, lon))
        d["pid"] = pid; d["elev_m"] = float(h.elev.iloc[0])
        d["grid_lat"] = float(h.grid_lat.iloc[0]); d["grid_lon"] = float(h.grid_lon.iloc[0])
        rows.append(d.reset_index().rename(columns={"index": "evening"}))
    df = pd.concat(rows, ignore_index=True)
    df["evening"] = pd.to_datetime(df["evening"]).dt.strftime("%Y-%m-%d")
    return df.dropna(subset=["fc_tmin", "era5_tmin"])


def in_window(df, a, b):
    return df[(df.evening >= a) & (df.evening <= b)]


def agreement(fc: np.ndarray, ref: np.ndarray) -> dict:
    r = ref - fc
    out = dict(n=int(len(fc)), bias_fc_minus_ref_c=round(float(np.mean(fc - ref)), 2),
               mae_c=round(float(np.mean(np.abs(r))), 2), sd_resid_c=round(float(np.std(r)), 2),
               corr=round(float(np.corrcoef(fc, ref)[0, 1]), 3),
               resid_ref_minus_fc_q10_q50_q90=[round(float(x), 2) for x in np.quantile(r, [0.1, 0.5, 0.9])])
    ev = {}
    for t in THRESHOLDS:
        e, f = ref <= t, fc <= t
        ev[str(t)] = dict(ref_freq=round(float(e.mean()), 3), fc_freq=round(float(f.mean()), 3),
                          hit_rate=round(float(f[e].mean()), 3) if e.any() else None,
                          false_alarm_rate=round(float(f[~e].mean()), 3) if (~e).any() else None)
    out["events_at_threshold_c"] = ev
    return out


def band(resid: np.ndarray, site_sd: float, seed: int = 0) -> dict:
    """Quantiles (1..99%) of the error term added to the forecast: reanalysis residual + N(0, site_sd)."""
    rng = np.random.default_rng(seed)
    mc = (np.repeat(resid, SITE_SD_MC_DRAWS) + rng.normal(0.0, site_sd, len(resid) * SITE_SD_MC_DRAWS))
    qs = np.arange(1, 100) / 100.0
    return dict(q=[round(float(x), 2) for x in qs], err_c=[round(float(x), 3) for x in np.quantile(mc, qs)],
                reanalysis_only_err_c=[round(float(x), 3) for x in np.quantile(resid, qs)])


def main():
    region = load("puno")
    site_sd = float(region.config["model"]["site_sd_prior_c"])
    fp_n = os.path.join(OUT, "puno_nights.csv.gz")
    df = build_nights(region)
    os.makedirs(OUT, exist_ok=True)
    df.round(2).to_csv(fp_n, index=False)

    res = dict(
        label="Forecast vs ERA5 REANALYSIS (not observations). Agreement, not skill.",
        forecast="Open-Meteo previous-runs ecmwf_ifs025 day-1 (temperature_2m_previous_day1), night 18-08 America/Lima",
        reference="Open-Meteo archive ERA5 (0.25 deg); ERA5-Land (0.1 deg) as a reference-uncertainty check",
        points=[dict(id=p["id"], place=p["place"], lat=p["lat"], lon=p["lon"],
                     elev_m=float(df[df.pid == p["id"]].elev_m.iloc[0])) for p in region.config["parcels"]],
        period=[START, END], seasons=SEASONS, season_types=SEASON_TYPES, site_sd_prior_c=site_sd,
        by_season={}, by_season_type={}, by_point={}, bands={}, cross_season_coverage={},
    )
    for s, (a, b) in SEASONS.items():
        d = in_window(df, a, b)
        if len(d) == 0:
            continue
        res["by_season"][s] = dict(
            day1_vs_era5=agreement(d.fc_tmin.values, d.era5_tmin.values),
            day1_vs_era5_land=agreement(d.fc_tmin.values, d.era5_land_tmin.values),
            era5_land_vs_era5=agreement(d.era5_land_tmin.values, d.era5_tmin.values),
            lead0_vs_era5=agreement(d.dropna(subset=["fc0_tmin"]).fc0_tmin.values, d.dropna(subset=["fc0_tmin"]).era5_tmin.values),
            era5_tmin_mean_c=round(float(d.era5_tmin.mean()), 2))
    for st, ss in SEASON_TYPES.items():
        d = pd.concat([in_window(df, *SEASONS[s]) for s in ss])
        res["by_season_type"][st] = dict(
            day1_vs_era5=agreement(d.fc_tmin.values, d.era5_tmin.values),
            era5_land_vs_era5=agreement(d.era5_land_tmin.values, d.era5_tmin.values))
        res["bands"][st] = band((d.era5_tmin - d.fc_tmin).values, site_sd)
        res["by_point"][st] = {pid: dict(n=int(len(g)), bias_fc_minus_era5_c=round(float((g.fc_tmin - g.era5_tmin).mean()), 2),
                                         mae_c=round(float((g.fc_tmin - g.era5_tmin).abs().mean()), 2),
                                         era5_tmin_mean_c=round(float(g.era5_tmin.mean()), 2),
                                         era5_nights_le_m2p5=round(float((g.era5_tmin <= -2.5).mean()), 3))
                               for pid, g in d.groupby("pid")}
        # cross-season stability of the REANALYSIS-ONLY 80% band: fit on one season, check the other (vs ERA5)
        a, b = (in_window(df, *SEASONS[s]) for s in ss)
        cov = {}
        for fit, test, name in ((a, b, f"{ss[0]}->{ss[1]}"), (b, a, f"{ss[1]}->{ss[0]}")):
            q10, q90 = np.quantile(fit.era5_tmin - fit.fc_tmin, [0.1, 0.9])
            r = test.era5_tmin - test.fc_tmin
            cov[name] = round(float(((r >= q10) & (r <= q90)).mean()), 3)
        res["cross_season_coverage"][st] = dict(
            note="Nominal 0.80 band built from forecast-minus-ERA5 residuals of one season, tested against ERA5 in the other. Band stability only; the product band is wider (adds the site prior).",
            coverage=cov)
    for st in SEASON_TYPES:
        e = np.array(res["bands"][st]["err_c"]); q = np.array(res["bands"][st]["q"])
        res["bands"][st]["width80_c"] = round(float(np.interp(0.9, q, e) - np.interp(0.1, q, e)), 2)
        r = np.array(res["bands"][st]["reanalysis_only_err_c"])
        res["bands"][st]["reanalysis_only_width80_c"] = round(float(np.interp(0.9, q, r) - np.interp(0.1, q, r)), 2)
    # What the forecast-only alert rule would do (in-sample band; compared with ERA5 events, NOT observed frost)
    thr = float(region.crop("papa")["alert_air_tmin_c"])
    for st, ss in SEASON_TYPES.items():
        d = pd.concat([in_window(df, *SEASONS[s]) for s in ss])
        q = np.array(res["bands"][st]["q"]); e = np.array(res["bands"][st]["err_c"])
        pf = np.clip(np.interp(thr - d.fc_tmin.values, e, q, left=0.0, right=1.0), 0, 1)
        lo = d.fc_tmin.values + np.interp(0.10, q, e)
        alert = pf >= 0.30; watch = (~alert) & (lo <= thr); ev = d.era5_tmin.values <= thr
        res.setdefault("rule_rates", {})[st] = dict(
            threshold_c=thr, n=int(len(d)),
            alerts_per_100_point_nights=round(100 * float(alert.mean()), 1),
            watches_per_100_point_nights=round(100 * float(watch.mean()), 1),
            era5_events_per_100=round(100 * float(ev.mean()), 1),
            era5_events_with_alert=round(float(alert[ev].mean()), 3) if ev.any() else None,
            era5_events_with_alert_or_watch=round(float((alert | watch)[ev].mean()), 3) if ev.any() else None,
            note="In-sample; 'events' are ERA5 reanalysis nights at or below the threshold, not observed frost.")
    with open(os.path.join(OUT, "puno_eval.json"), "w") as fh:
        json.dump(res, fh, indent=1, ensure_ascii=False)
    for st in SEASON_TYPES:
        x = res["by_season_type"][st]
        print(st, json.dumps(dict(day1_vs_era5={k: x["day1_vs_era5"][k] for k in ("n", "bias_fc_minus_ref_c", "mae_c", "corr")},
                                  era5_land_vs_era5_mae=x["era5_land_vs_era5"]["mae_c"],
                                  width80=res["bands"][st]["width80_c"], reanalysis_only_width80=res["bands"][st]["reanalysis_only_width80_c"],
                                  cov=res["cross_season_coverage"][st]["coverage"])))


if __name__ == "__main__":
    main()
