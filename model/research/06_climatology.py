"""Frost-date climatology per SMN station from the full daily record (1991-2025).

For each station-year with good coverage:
  last spring frost  = last date Jan 1..Jul 15 with Tmin <= tau   (none -> DOY 0)
  first autumn frost = first date Jul 16..Dec 31 with Tmin <= tau (none -> DOY 366)
for tau in TAUS (so a parcel that runs delta degC colder than the station uses tau = +delta at the station).
Percentiles over years: last-spring p50/p90, first-autumn p10/p50, frost-free days p50.
Output: ../src/helada_model/artifacts/climatology.json
"""
import os, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "..", "..", "data")
ART = os.path.join(HERE, "..", "src", "helada_model", "artifacts")
TAUS = [-3, -2, -1, 0, 1, 2, 3]
Y0, Y1 = 1991, 2025

obs = pd.read_csv(f"{D}/smn/obs_daily.csv.gz", dtype={"station": str}, parse_dates=["date"], usecols=["station", "date", "tmin"])
obs = obs[obs.date.dt.year.between(Y0, Y1)]
obs["y"] = obs.date.dt.year; obs["doy"] = obs.date.dt.dayofyear; obs["m"] = obs.date.dt.month
st = pd.read_csv(f"{D}/smn/stations.csv", dtype={"station": str}).set_index("station")

out = {"years": [Y0, Y1], "taus": TAUS, "stations": {}}
for s, g in obs.groupby("station"):
    rows = []
    for y, gy in g.groupby("y"):
        spr = gy[gy.doy <= 196]; aut = gy[gy.doy > 196]
        cov_s = spr[spr.m.between(2, 6)].shape[0] / 150; cov_a = aut[aut.m.between(8, 12)].shape[0] / 153
        r = {"y": y}
        for t in TAUS:
            r[f"ls{t}"] = (spr[spr.tmin <= t].doy.max() if (spr.tmin <= t).any() else 0) if cov_s >= 0.8 else np.nan
            r[f"fa{t}"] = (aut[aut.tmin <= t].doy.min() if (aut.tmin <= t).any() else 366) if cov_a >= 0.8 else np.nan
        rows.append(r)
    t = pd.DataFrame(rows)
    ny = int(t[["ls0", "fa0"]].notna().all(axis=1).sum())
    if ny < 10:
        continue
    rec = {"lat": float(st.loc[s, "lat"]), "lon": float(st.loc[s, "lon"]), "alt": float(st.loc[s, "alt"]),
           "name": str(st.loc[s, "name"]), "n_years": ny, "tau": {}}
    for tau in TAUS:
        ls, fa = t[f"ls{tau}"].dropna(), t[f"fa{tau}"].dropna()
        both = t[[f"ls{tau}", f"fa{tau}"]].dropna()
        rec["tau"][str(tau)] = dict(ls_p50=float(np.percentile(ls, 50)), ls_p90=float(np.percentile(ls, 90)),
                                    fa_p10=float(np.percentile(fa, 10)), fa_p50=float(np.percentile(fa, 50)),
                                    ffd_p50=float(np.percentile(both.iloc[:, 1] - both.iloc[:, 0], 50)))
    out["stations"][s] = rec
json.dump(out, open(f"{ART}/climatology.json", "w"), separators=(",", ":"))
print("stations", len(out["stations"]), "size KB", os.path.getsize(f"{ART}/climatology.json") / 1e3)
c = pd.DataFrame({s: v["tau"]["0"] | {"n": v["n_years"], "name": v["name"]} for s, v in out["stations"].items()}).T
print(c.sort_values("ffd_p50").head(12).to_string()); print(c.sort_values("ffd_p50").tail(5).to_string())
print(c[["ls_p50", "ls_p90", "fa_p10", "fa_p50", "ffd_p50"]].astype(float).describe().round(0).to_string())
