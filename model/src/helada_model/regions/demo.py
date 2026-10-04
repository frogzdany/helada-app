"""Country-swap runner: the same pipeline for any region config.

    uv run python -m helada_model.regions.demo puno --date 2025-01-13
    uv run python -m helada_model.regions.demo puno --date 2025-07-10 --crop papa_amarga
    uv run python -m helada_model.regions.demo toluca --date 2025-11-14

Prints one row per demo parcel: day-1 forecast Tmin, Helada Tmin with its 80% band, the support regime,
P(Tmin <= the crop's alert threshold) and what the alert rule would do.

- mode `helada_model` (Toluca): helada_model.predict() with the region's forecast; the backtested product.
- mode `forecast_only` (Puno): there is no station data, so nothing can be learned or checked at the parcel.
  Helada Tmin IS the forecast (no correction). The band is the measured forecast-vs-ERA5 spread (reanalysis, not
  observations) plus a site-microclimate prior, so it is a floor, not a validated interval.

Forecast input: the cached nights table `regions/out/<name>_nights.csv.gz` when it has the date (offline), else the
Open-Meteo Previous Runs API (day-1 lead, ecmwf_ifs025) for past nights, or the Forecast API for today onward.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import date as _date, timedelta

import numpy as np
import pandas as pd

from . import Region, load

PREV_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
LIVE_URL = "https://api.open-meteo.com/v1/forecast"
VARS = ["temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m"]
P_ALERT = 0.30     # same rule as the Toluca contract: ALERT if P >= 0.30; WATCH if band reaches the threshold


def _http_json(url: str, params: dict, timeout: float = 60.0):
    with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def _cached_nights(region: Region, date: str) -> dict | None:
    fp = region.path.parent / "out" / f"{region.name}_nights.csv.gz"
    if not fp.exists():
        return None
    d = pd.read_csv(fp)
    d = d[d.evening == date]
    ids = [p["id"] for p in region.config["parcels"]]
    if set(ids) - set(d.pid):
        return None
    out = {}
    for _, r in d.iterrows():
        out[r.pid] = dict(tmin_c=float(r.fc_tmin), dew_c=float(r.fc_dew), cloud_pct=float(r.fc_cloud),
                          wind_kmh=float(r.fc_wind), tmax_prev_c=float(r.fc_tmax_prev),
                          era5_tmin_c=None if pd.isna(r.get("era5_tmin")) else float(r.era5_tmin))
    return out


def fetch_forecasts(region: Region, date: str, today: _date | None = None) -> tuple[dict, str]:
    """{parcel_id: scalar forecast dict} for the night starting on the evening of `date`, plus a source label."""
    from ..features import nights_from_hourly
    cached = _cached_nights(region, date)
    if cached is not None:
        return cached, f"cached Open-Meteo {region.config['model']['forecast_source']} day-1 (regions/out/{region.name}_nights.csv.gz)"
    today = today or _date.today()
    d0 = pd.Timestamp(date)
    model = region.config["model"]["forecast_source"]
    live = d0.date() >= today - timedelta(days=1)
    ps = region.config["parcels"]
    q = dict(latitude=",".join(f"{p['lat']:.5f}" for p in ps), longitude=",".join(f"{p['lon']:.5f}" for p in ps),
             start_date=d0.strftime("%Y-%m-%d"), end_date=(d0 + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
             timezone=region.config["timezone"], models=model,
             hourly=",".join(VARS if live else [f"{v}_previous_day1" for v in VARS]))
    j = _http_json(LIVE_URL if live else PREV_URL, q)
    if isinstance(j, dict):
        j = [j]
    if len(j) != len(ps):
        raise RuntimeError(f"Open-Meteo returned {len(j)} locations for {len(ps)} parcels")
    out = {}
    for p, x in zip(ps, j):
        h = pd.DataFrame(x["hourly"])
        h.columns = [c.replace("_previous_day1", "").replace(f"_{model}", "") for c in h.columns]
        n = nights_from_hourly(h)
        row = n[n.date == d0 + pd.Timedelta(days=1)]
        if row.empty:
            raise RuntimeError(f"no complete night window for {p['id']} on {date}")
        r = row.iloc[0]
        out[p["id"]] = dict(tmin_c=float(r.fc_tmin), dew_c=float(r.fc_dew), cloud_pct=float(r.fc_cloud),
                            wind_kmh=float(r.fc_wind), tmax_prev_c=float(r.fc_tmax_prev), era5_tmin_c=None)
    label = "live forecast" if live else "day-1 archived run"
    return out, f"Open-Meteo {model} {label} (network)"


def _band_table(region: Region, season_type: str):
    cal = region.calibration()
    if cal and season_type in cal.get("bands", {}):
        b = cal["bands"][season_type]
        return np.array(b["q"]), np.array(b["err_c"]), f"measured ({season_type} season, forecast vs ERA5) + site prior"
    sd = math.hypot(float(region.config["model"].get("site_sd_prior_c", 2.3)), 2.0)
    q = np.arange(1, 100) / 100.0
    from statistics import NormalDist
    return q, np.array([NormalDist(0, sd).inv_cdf(x) for x in q]), f"UNCALIBRATED fallback N(0, {sd:.1f})"


def _decision(p: float, lo: float, thr: float, in_field: bool, anchored: bool | None = None) -> str:
    if not in_field:
        return "no crop alert (not in field)"
    if p >= P_ALERT:
        return "ALERT"
    if lo <= thr and not anchored:
        return "WATCH (text)"
    return "-"


def run(name: str, date: str, crop: str | None = None, forecasts: dict | None = None, source: str | None = None) -> dict:
    """Compute the table for a region/night. Returns a dict (rows + header facts); `main` prints it."""
    region = load(name)
    c = region.crop(crop)
    thr = float(c["alert_air_tmin_c"])
    in_field = region.crop_in_field(c["id"], date)
    if forecasts is None:
        forecasts, source = fetch_forecasts(region, date)
    rows = []
    if region.mode == "helada_model":
        import helada_model as hm
        parcels = region.parcels()
        fc = {k: {kk: vv for kk, vv in v.items() if kk != "era5_tmin_c"} for k, v in forecasts.items()}
        for p, f in zip(parcels, hm.predict(parcels, date, forecast=fc)):
            anch = f.drivers["support_station_anchored"] == 1.0
            rows.append(dict(parcel=p.parcel_id, place=p.municipality, fc_tmin=f.grid_tmin_c, tmin=f.tmin_c,
                             lo=f.tmin_lo_c, hi=f.tmin_hi_c, support=hm.support_of(f), p=f.p_frost,
                             action=_decision(f.p_frost, f.tmin_lo_c, thr, in_field, anch), ref=None))
        band_label = "calibrated on held-out SMN station-nights (observations)"
    else:
        season_type = "crop" if in_field else "cold"
        q, err, band_label = _band_table(region, season_type)
        for p in region.config["parcels"]:
            f = forecasts[p["id"]]
            t = float(f["tmin_c"])
            lo, hi = t + float(np.interp(0.10, q, err)), t + float(np.interp(0.90, q, err))
            pf = float(np.clip(np.interp(thr - t, err, q, left=0.0, right=1.0), 0.0, 1.0))
            rows.append(dict(parcel=p["id"], place=p.get("place", ""), fc_tmin=round(t, 2), tmin=round(t, 2),
                             lo=round(lo, 2), hi=round(hi, 2), support="forecast-only", p=round(pf, 3),
                             action=_decision(pf, lo, thr, in_field), ref=f.get("era5_tmin_c")))
    cal = region.calibration() if region.mode == "forecast_only" else None
    return dict(region=region.name, display_name=region.config.get("display_name", region.name), date=date,
                mode=region.mode, crop=c["id"], crop_name=c.get("name_es", c["id"]), threshold_c=thr,
                crit_plant_c=c["crit_plant_c"], in_field=in_field, stations=region.stations_available,
                source=source, band=band_label, rows=rows, calibration_summary=_cal_summary(cal),
                precondition=region.config["model"].get("precondition", ""),
                program=region.config["program"]["name"], packet_status=region.config["program"].get("packet_status", ""))


def _cal_summary(cal: dict | None) -> dict | None:
    if not cal:
        return None
    out = {}
    for st, v in cal.get("by_season_type", {}).items():
        a = v["day1_vs_era5"]
        out[st] = dict(n=a["n"], mae_c=a["mae_c"], bias_c=a["bias_fc_minus_ref_c"],
                       era5_land_vs_era5_mae_c=v["era5_land_vs_era5"]["mae_c"],
                       width80_c=cal["bands"][st]["width80_c"])
    return out


def format_table(res: dict) -> str:
    L = []
    L.append(f"Helada | {res['display_name']} | night of {res['date']} | crop: {res['crop_name']}")
    L.append(f"mode: {res['mode']} | station data: {'yes' if res['stations'] else 'NO'} | forecast: {res['source']}")
    L.append(f"alert threshold: air Tmin <= {res['threshold_c']:+.1f} C (plant critical {res['crit_plant_c']} C, draft) | band: {res['band']}")
    if not res["in_field"]:
        L.append(f"NOTE: {res['crop']} is normally not in the field on {res['date']}; the table is shown, no crop alert is sent.")
    show_ref = any(r["ref"] is not None for r in res["rows"])
    hdr = f"{'parcel':<7}{'place':<28}{'fcst':>6}  {'Helada Tmin [80% band]':<24}{'support':<18}{'P(<=thr)':>9}  action"
    if show_ref:
        hdr += "   | ERA5*"
    L.append(hdr); L.append("-" * len(hdr))
    for r in res["rows"]:
        s = (f"{r['parcel']:<7}{r['place'][:27]:<28}{r['fc_tmin']:>6.1f}  "
             f"{r['tmin']:>5.1f} [{r['lo']:>5.1f}, {r['hi']:>5.1f}]{'':<8}{r['support']:<18}{r['p']:>9.2f}  {r['action']}")
        if show_ref:
            s += f"   | {r['ref']:.1f}" if r["ref"] is not None else "   | -"
        L.append(s)
    if res["mode"] == "forecast_only":
        L.append("")
        L.append("Helada Tmin = the forecast. No correction is learned or claimed: there are no station observations here.")
        cs = res["calibration_summary"]
        if cs:
            for st, v in cs.items():
                L.append(f"  measured vs ERA5 REANALYSIS (not observations), {st} season, {v['n']} point-nights: "
                         f"MAE {v['mae_c']:.2f} C, bias {v['bias_c']:+.2f} C; ERA5-Land vs ERA5 MAE {v['era5_land_vs_era5_mae_c']:.2f} C; "
                         f"band width {v['width80_c']:.1f} C")
        if show_ref:
            L.append("  * ERA5 = reanalysis at ~31 km for the same night, for reference only. Both are grid-scale; neither sees the parcel.")
    L.append(f"PRECONDITION: {res['precondition']}")
    L.append(f"Loss program: {res['program']}. Packet: {res['packet_status']}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m helada_model.regions.demo", description=__doc__.splitlines()[0])
    ap.add_argument("region")
    ap.add_argument("--date", required=True, help="local evening date YYYY-MM-DD")
    ap.add_argument("--crop", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = run(a.region, a.date, crop=a.crop)
    print(json.dumps(res, indent=1, ensure_ascii=False) if a.json else format_table(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
