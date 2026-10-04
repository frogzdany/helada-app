"""Download + parse CONAGUA/SMN daily climatology files for Toluca-valley-region stations (full history).

Source: https://smn.conagua.gob.mx/tools/RESOURCES/Normales_Climatologicas/Diarios/mex/dia{ID}.txt
Raw files go to a temp dir (not kept). Output: ../data/smn/obs_daily.csv.gz (station,date,tmin,tmax), stations.csv
Station metadata list (all 353 Edomex files) is `data/smn/smn_edomex_stations.csv`.
QC: tmin in [-15,20], tmin<=tmax, station-month robust outlier cut, stuck runs >=7 removed.
"""
import os, re, sys, tempfile, subprocess, concurrent.futures as cf
import numpy as np, pandas as pd, requests

HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "..", "..", "data", "smn")
BOX = dict(lat=(18.9, 20.1), lon=(-100.3, -99.2), alt_min=2300)
URL = "https://smn.conagua.gob.mx/tools/RESOURCES/Normales_Climatologicas/Diarios/mex/dia{}.txt"

meta = pd.read_csv(f"{D}/smn_edomex_stations.csv", dtype={"station": str})
box = meta[meta.lat.between(*BOX["lat"]) & meta.lon.between(*BOX["lon"]) & (meta.alt >= BOX["alt_min"])]
tmp = tempfile.mkdtemp()

def get(sid):
    p = f"{tmp}/dia{sid}.txt"
    for _ in range(3):
        try:
            r = requests.get(URL.format(sid), headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
            if r.ok and "ESTACI" in r.text[:3000]:
                open(p, "wb").write(r.content); return sid, True
        except Exception:
            pass
    return sid, False

with cf.ThreadPoolExecutor(12) as ex:
    ok = [s for s, g in ex.map(get, box.station) if g]
print("downloaded", len(ok), "of", len(box))

rows = []
for sid in ok:
    txt = open(f"{tmp}/dia{sid}.txt", encoding="latin-1", errors="ignore").read()
    data = re.findall(r"^(\d{4}-\d{2}-\d{2})\t([^\t]*)\t([^\t]*)\t([^\t]*)\t([^\t\n]*)", txt, re.M)
    df = pd.DataFrame(data, columns=["date", "prcp", "evap", "tmax", "tmin"])
    df["tmin"] = pd.to_numeric(df.tmin, errors="coerce"); df["tmax"] = pd.to_numeric(df.tmax, errors="coerce")
    df = df.dropna(subset=["tmin"]); df["station"] = sid
    rows.append(df[["station", "date", "tmin", "tmax"]])
obs = pd.concat(rows); obs["date"] = pd.to_datetime(obs.date, errors="coerce"); obs = obs.dropna(subset=["date"])
n0 = len(obs)
obs = obs[obs.tmin.between(-15, 20)]
obs = obs[~(obs.tmax.notna() & (obs.tmax < obs.tmin))]
obs["ym"] = obs.date.dt.to_period("M")
med = obs.groupby(["station", "ym"]).tmin.transform("median")
mad = obs.groupby(["station", "ym"]).tmin.transform(lambda s: (s - s.median()).abs().median())
obs = obs[(obs.tmin - med).abs() <= 4 * mad + 3]
obs = obs.sort_values(["station", "date"])
grp = (obs.tmin != obs.groupby("station").tmin.shift()).cumsum()
obs = obs[obs.groupby(grp).tmin.transform("size") < 7].drop(columns="ym")
print(f"QC kept {len(obs)}/{n0}")
obs.to_csv(f"{D}/obs_daily.csv.gz", index=False)
st = box[box.station.isin(ok)].copy()
cnt = obs.groupby("station").agg(first=("date", "min"), last=("date", "max"), n=("tmin", "size"),
                                 n_2024=("date", lambda d: (d >= "2024-02-01").sum()))
st = st.set_index("station").join(cnt).reset_index()
st.to_csv(f"{D}/stations.csv", index=False)
print(st.sort_values("n_2024", ascending=False).head(100).to_string())
