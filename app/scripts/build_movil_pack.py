"""Build the phone page's data: the site pack(s), the model files and the parity fixtures.

    cd app && uv run python scripts/build_movil_pack.py            # packs + models + fixtures + sw.js version
    cd app && uv run python scripts/build_movil_pack.py --stamp    # after editing only page files (html/css/js)

Writes, under static/movil/data/:
  pack.json        shipped model: per-parcel terrain + station anchoring, calibration, logger curve, alert rule,
                   advisory table (the fixed list of things the phone may say; plus its English reading aid from
                   backend/advisory.en.yaml), fail-safe thresholds
  pack-demo.json   same for variant "holdout_2025_26" + the archived forecasts of the replay night (14 Nov 2025)
  models/, models-demo/   the LightGBM text artifacts, copied byte for byte (gzip)
and tests/fixtures/movil_parity.json: inputs + outputs of the Python model, replayed by tests/movil_parity.mjs.

Terrain features need the DEM (865 KB) and are computed here once per parcel, which is what "registering a parcel"
means for the phone. Everything the phone then needs is in the pack.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

import helada_model as hm  # noqa: E402
from helada_model import core, features, logger as lg  # noqa: E402

from backend import alerts  # noqa: E402

OUT = APP / "static" / "movil" / "data"
FIX = APP / "tests" / "fixtures" / "movil_parity.json"
ART = Path(core.ART)
FILES = {"anchor": "model_anchor.txt.gz", "transfer_wx": "model_transfer_wx.txt.gz", "transfer_terrain": "model_transfer_terrain.txt.gz"}
VARIANTS = {"shipped": (None, "models", "pack.json"), "demo": ("holdout_2025_26", "models-demo", "pack-demo.json")}
BIG = 1e30

# Thresholds of the phone's fail-safe (helada-model.js judge()).
FAILSAFE = dict(
    fresh_lead_days=1,     # a forecast downloaded today or yesterday is what the model was trained on (day-1 run)
    max_lead_days=3,       # older than this: the phone says "no estoy seguro" whatever the number
    stale_gray_p=0.15,     # between fresh and max: any P(frost) at or above this is "no estoy seguro"
    max_fallback=2,        # this many weather inputs missing from the forecast: "no estoy seguro"
)
# Who the phone sends the farmer to: the same contact the bot gives (advisory.yaml `contact`). FICTIONAL in the
# demo pack: a real deployment sets the officer of the Delegación.
CONTACT = dict(alerts.advisory()["contact"])


def clean(o):
    """JSON-safe: +-inf -> +-1e30, NaN -> None, numpy scalars -> python."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        if math.isnan(f):
            return None
        if math.isinf(f):
            return BIG if f > 0 else -BIG
        return f
    if isinstance(o, np.integer):
        return int(o)
    return o


def roster() -> list[dict]:
    return json.loads((APP / "backend" / "data" / "roster.json").read_text(encoding="utf-8"))["parcels"]


def parcel_obj(p: dict) -> hm.Parcel:
    return hm.Parcel(parcel_id=p["parcel_id"], lat=p["lat"], lon=p["lon"], crop=p.get("crop", "maiz_temporal"))


def site_entry(p: dict, A) -> dict:
    t, sid, dkm, anch = core._site(parcel_obj(p), A)
    st = A["stations"].loc[sid]
    e = dict(parcel_id=p["parcel_id"], owner_name=p.get("owner_name", ""), municipality=p.get("municipality", ""),
             crop=p.get("crop", "maiz_temporal"), area_ha=p.get("area_ha", 0), phone=p.get("phone", ""),
             lat=p["lat"], lon=p["lon"], terrain={k: float(v) for k, v in t.items()}, anchored=bool(anch),
             station=dict(id=str(sid), name=str(st["name"]).title(), km=round(float(dkm), 2)))
    if anch:
        feats = {k: float(st[k]) for k in A["meta"]["features"]["anchor"] if k not in features.WEATHER}
        feats.update(lat=float(st.lat), lon=float(st.lon))
        e["station"].update(a=float(st.a), b=float(st.b), feats=feats)
    return e


def advisory_block() -> dict:
    adv = alerts.advisory()
    crops = {}
    for name, c in adv["crops"].items():
        crops[name] = dict(name=c.get("name", name), stages=[
            dict(stage=s["stage"], months=s["months"], actions=[dict(text=a["text"], source=a.get("source")) for a in s["actions"]])
            for s in c["stages"]])
    meta = adv.get("meta") or {}
    block = dict(signed=bool(meta.get("signed_by")), unsigned_label=meta.get("unsigned_label", "Borrador sin firma agronómica"),
                 sources=adv.get("sources", {}), crops=crops)
    # English reading aid for reviewers (advisory.en.yaml): same crops, stages and actions, in the same order.
    en_file = APP / "backend" / "advisory.en.yaml"
    if en_file.exists():
        en = yaml.safe_load(en_file.read_text(encoding="utf-8"))
        for name, c in crops.items():
            e = en["crops"][name]
            if [len(s["actions"]) for s in c["stages"]] != [len(s["actions"]) for s in e["stages"]]:
                raise SystemExit(f"advisory.en.yaml is out of step with advisory.yaml for crop {name!r}")
        block["en"] = dict(unsigned_label=en.get("unsigned_label"), crops={
            name: dict(name=e.get("name", name), stages=[dict(stage=s["stage"], actions=list(s["actions"])) for s in e["stages"]])
            for name, e in en["crops"].items() if name in crops})
    return block


LOGGER_KEYS = ("tmin_c", "dew_c", "cloud_pct", "wind_kmh", "tmax_prev_c", "grid_elev_m")
LOGGER_ID = "15390"


def demo_logger_data():
    """The worked example of backend/data/demo_logger.json: SMN 15390 (Jocotitlan), a station the held-out model
    never saw, standing in for a parcel's thermometer. Returns (site dict, replay night, [(date, tmin, forecast)])."""
    D = json.loads((APP / "backend" / "data" / "demo_logger.json").read_text(encoding="utf-8"))
    until = D["replay_night"]
    fx = {d: {k: f.get(k) for k in LOGGER_KEYS} for d, f in D["forecasts"].items()}
    nights = [(r["night_date"], float(r["tmin_c"]), fx[r["night_date"]]) for r in D["observations"]
              if r["night_date"] < until and r["night_date"] in fx and r["tmin_c"] is not None]
    truth = next((r["tmin_c"] for r in D["observations"] if r["night_date"] == until), None)
    site = dict(parcel_id=LOGGER_ID, owner_name="", municipality=D["site"]["municipality"], crop="maiz_temporal",
                lat=D["site"]["lat"], lon=D["site"]["lon"])
    return D, site, until, fx[until], truth, sorted(nights)


def logger_steps(n: int) -> list[int]:
    return [k for k in (0, 7, 14, 30) if k < n] + [n]


def demo_logger_block(A) -> dict:
    D, site, until, fd, truth, nights = demo_logger_data()
    entry = site_entry(site, A)
    if entry["anchored"]:
        raise SystemExit("the logger example must be a site with no station nearby")
    entry["name"] = "Jocotitlán"
    return dict(site=entry, station=D["site"]["station"], night_date=until, forecast=fd, observed_tmin_c=truth,
                steps=logger_steps(len(nights)), season_check=D.get("season_check"),
                nights=[dict(date=d, tmin_c=t, forecast=f) for d, t, f in nights])


def build_pack(key: str) -> dict:
    variant, models_dir, _ = VARIANTS[key]
    A = core._art(variant)
    m = A["meta"]
    curve = lg.curve()
    src = ART / (core.VARIANTS[variant])
    dst = OUT / models_dir
    dst.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for k, fn in FILES.items():
        shutil.copyfile(src / fn, dst / fn)
        sizes[k] = (dst / fn).stat().st_size
    pack = dict(
        version=1, variant=variant or "shipped", built_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        models=dict(dir=f"data/{models_dir}/", files=FILES, bytes=sizes, total_bytes=sum(sizes.values())),
        meta=dict(features=m["features"], anchor_rule=m["anchor_rule"], calibration=m["calibration"],
                  forecast_source=m["forecast_source"], trained_on=m.get("trained_on"), n_nights=m.get("n_nights"),
                  n_stations=m.get("n_stations"), domain=core.DOMAIN),
        logger=dict(hyper=curve["hyper"], levels=curve["levels"], ceiling=curve.get("ceiling")),
        alert=dict(p_frost_min=alerts.P_FROST_MIN, tmin_lo_max=alerts.TMIN_LO_MAX, logger_full_season=alerts.LOGGER_FULL_SEASON),
        failsafe=FAILSAFE, contact=CONTACT, advisory=advisory_block(),
        parcels=[site_entry(p, A) for p in roster()],
    )
    if key == "demo":
        rp = json.loads((APP / "backend" / "data" / "demo_replay.json").read_text(encoding="utf-8"))
        nights = {}
        for pid, v in rp["parcels"].items():
            fd = {k: v["forecast"].get(k) for k in ("grid_tmin_c", "dew_c", "cloud_cover", "wind_kmh", "tmax_prev_c", "grid_elev_m")}
            nights[pid] = dict(forecast=fd, observed_tmin_c=v.get("observed_tmin_c"),
                               station=(v.get("station") or {}).get("name"))
        pack["demo"] = dict(label=rp["label"], night_date=rp["night_date"], observation_date=rp["observation_date"],
                            forecast_source=rp["forecast_source"], observation_source=rp["observation_source"],
                            note=rp["holdout_note"], parcels=nights, logger=demo_logger_block(A))
    return clean(pack)


# --------------------------------------------------------------------------- parity fixtures
FORECASTS = [
    dict(tmin_c=1.5, dew_c=-3.0, cloud_pct=5, wind_kmh=3, tmax_prev_c=20, grid_elev_m=2650),
    dict(tmin_c=6.0, dew_c=4.0, cloud_pct=80, wind_kmh=12, tmax_prev_c=18, grid_elev_m=2700),
    dict(tmin_c=-1.0, dew_c=-6.0, cloud_pct=0, wind_kmh=1.5, tmax_prev_c=19),
    dict(tmin_c=3.0),
    dict(tmin_c=4.0, rh=70, wind_ms=1.2, cloud_pct=30, tmax_prev_c=21, grid_elev_m=2500),
    dict(tmin_c=10.0, dew_c=8.0, cloud_pct=100, wind_kmh=20, tmax_prev_c=17, grid_elev_m=2600),
    dict(tmin_c=0.2, dew_c=-8.0, cloud_pct=12, wind_kmh=4.5, tmax_prev_c=23.5, grid_elev_m=2580),
]
DATES = ["2026-01-15", "2026-10-03", "2025-11-14", "2026-05-02", "2025-12-31", "2026-02-28", "2026-07-20"]


def fc_out(f) -> dict:
    return dict(tmin_c=f.tmin_c, tmin_lo_c=f.tmin_lo_c, tmin_hi_c=f.tmin_hi_c, p_frost=f.p_frost, grid_tmin_c=f.grid_tmin_c,
                support=hm.support_of(f), fallback=f.drivers["forecast_fallback"])


def build_fixtures(packs: dict) -> dict:
    cases = []
    for key, (variant, _, _) in VARIANTS.items():
        for i, p in enumerate(roster()):
            for j, fd in enumerate(FORECASTS):
                date = DATES[(i + j) % len(DATES)]
                f = hm.predict([parcel_obj(p)], date, forecast=fd, variant=variant)[0]
                cases.append(dict(pack=key, parcel_id=p["parcel_id"], date=date, forecast=fd, logger=None, expect=fc_out(f)))
    # the replay night with its archived forecasts, held-out variant
    demo = packs["demo"]["demo"]
    for p in roster():
        fd = demo["parcels"][p["parcel_id"]]["forecast"]
        f = hm.predict([parcel_obj(p)], demo["night_date"], forecast=fd, variant="holdout_2025_26")[0]
        cases.append(dict(pack="demo", parcel_id=p["parcel_id"], date=demo["night_date"], forecast=fd, logger=None, expect=fc_out(f)))
    # logger-anchored: a parcel with no station nearby and its own thermometer readings
    rng = np.random.default_rng(7)
    A = core._art(None)
    for p in roster():
        if core._site(parcel_obj(p), A)[3]:
            continue
        for k in (1, 4, 9, 30):
            days = pd.date_range("2026-01-02", periods=k).strftime("%Y-%m-%d").tolist()
            fx = {d: dict(tmin_c=float(rng.uniform(-1, 7)), dew_c=float(rng.uniform(-8, 3)), cloud_pct=float(rng.uniform(0, 90)),
                          wind_kmh=float(rng.uniform(1, 12)), tmax_prev_c=float(rng.uniform(17, 24)), grid_elev_m=2600.0) for d in days}
            obs = [(d, round(float(fx[d]["tmin_c"] - rng.uniform(0.5, 4.0)), 1)) for d in days]
            sc = hm.calibrate_site(parcel_obj(p), obs, forecasts=fx)
            for fd in FORECASTS[:3]:
                f = hm.predict([parcel_obj(p)], "2026-02-10", forecast=fd, sites={p["parcel_id"]: sc})[0]
                cases.append(dict(pack="shipped", parcel_id=p["parcel_id"], date="2026-02-10", forecast=fd,
                                  logger=[dict(date=n["date"], tmin_c=n["tmin_c"], forecast=n["forecast"]) for n in sc.nights],
                                  expect=fc_out(f) | dict(logger_a=sc.a_c, logger_b=sc.b_c)))
    # the worked thermometer example of the demo pack, at each step of the stepper
    D, site, until, fd, truth, nights = demo_logger_data()
    mp = parcel_obj(site)
    for k in logger_steps(len(nights)):
        use = nights[:k]
        sites = None
        if use:
            sc = hm.calibrate_site(mp, [(d, t) for d, t, _ in use], forecasts={d: f for d, _, f in use}, variant="holdout_2025_26")
            assert sc.n_nights == k, (sc.n_nights, k, sc.rejected)
            sites = [sc]
        f = hm.predict([mp], until, forecast=fd, variant="holdout_2025_26", sites=sites)[0]
        cases.append(dict(pack="demo", parcel_id=LOGGER_ID, demo_logger=True, date=until, forecast=fd,
                          logger=[dict(date=d, tmin_c=t, forecast=fc) for d, t, fc in use], expect=fc_out(f)))
    # hourly -> night aggregation
    t = pd.date_range("2026-01-10 00:00", "2026-01-13 23:00", freq="h")
    h = pd.DataFrame(dict(time=t.strftime("%Y-%m-%dT%H:%M"),
                          temperature_2m=np.round(8 + 9 * np.sin((t.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.4, len(t)), 1),
                          dew_point_2m=np.round(rng.uniform(-6, 4, len(t)), 1), cloud_cover=np.round(rng.uniform(0, 100, len(t))),
                          wind_speed_10m=np.round(rng.uniform(0.5, 14, len(t)), 1)))
    n = features.nights_from_hourly(h)
    nights = {}
    for _, r in n.iterrows():
        ev = (pd.Timestamp(r.date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        nights[ev] = dict(tmin_c=float(r.fc_tmin), dew_c=float(r.fc_dew), cloud_pct=float(r.fc_cloud), wind_kmh=float(r.fc_wind),
                          tmax_prev_c=None if pd.isna(r.fc_tmax_prev) else float(r.fc_tmax_prev))
    hourly = dict(hourly={c + ("" if c == "time" else "_ecmwf_ifs025"): h[c].tolist() for c in h.columns}, expect=nights)
    return clean(dict(cases=cases, hourly=hourly))


def shell_version() -> str:
    """Hash of every file the service worker caches. Any change to the page gives a new version."""
    import hashlib
    import re
    movil = APP / "static" / "movil"
    sw = (movil / "sw.js").read_text(encoding="utf-8")
    files = re.findall(r'"([^"]+)"', sw[sw.index("const SHELL = ["):sw.index("];", sw.index("const SHELL = ["))])
    text = {".html", ".js", ".css", ".json", ".svg", ".webmanifest", ".txt"}
    h = hashlib.sha256()
    for rel in sorted(files):
        data = (movil / rel).read_bytes()
        if Path(rel).suffix in text:       # a Windows checkout may hold CRLF: hash the text as git stores it
            data = data.replace(b"\r\n", b"\n")
        h.update(rel.encode()); h.update(b"\0"); h.update(data); h.update(b"\0")
    return "helada-movil-" + h.hexdigest()[:12]


def stamp_sw() -> str:
    """Write the shell hash into sw.js (const VERSION). Run after ANY change under static/movil."""
    import re
    path = APP / "static" / "movil" / "sw.js"
    v = shell_version()
    src = path.read_text(encoding="utf-8")
    new = re.sub(r'const VERSION = "[^"]*";', f'const VERSION = "{v}";', src, count=1)
    if new != src:
        path.write_text(new, encoding="utf-8")
    return v


def main() -> None:
    if "--stamp" in sys.argv:          # page files changed, packs did not: only refresh the service worker version
        print("sw.js VERSION:", stamp_sw())
        return
    OUT.mkdir(parents=True, exist_ok=True)
    packs = {}
    for key, (_, _, fn) in VARIANTS.items():
        packs[key] = build_pack(key)
        (OUT / fn).write_text(json.dumps(packs[key], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{fn}: {(OUT / fn).stat().st_size / 1024:.0f} KB, models {packs[key]['models']['total_bytes'] / 1024:.0f} KB, "
              f"{sum(p['anchored'] for p in packs[key]['parcels'])}/{len(packs[key]['parcels'])} parcels station-anchored")
    fx = build_fixtures(packs)
    FIX.parent.mkdir(parents=True, exist_ok=True)
    FIX.write_text(json.dumps(fx, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{FIX.name}: {len(fx['cases'])} cases, {len(fx['hourly']['expect'])} aggregated nights")
    print("sw.js VERSION:", stamp_sw())


if __name__ == "__main__":
    main()
