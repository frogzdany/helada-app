"""Station-night table: SMN observed Tmin (08:00 reading, morning date D) + day-1 forecast night features + terrain."""
import os, sys, glob
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "..", "..", "data")
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from helada_model.features import nights_from_hourly, add_derived

def build(model):
    fs = []
    for p in sorted(glob.glob(f"{D}/forecasts/{model}/*.csv.gz")):
        h = pd.read_csv(p); h["station"] = os.path.basename(p).split(".")[0]; fs.append(h)
    h = pd.concat(fs)
    ge = h.groupby("station").grid_elev.first()
    f = nights_from_hourly(h[["station", "time", "temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m"]])
    f["grid_elev"] = f.station.map(ge)
    obs = pd.read_csv(f"{D}/smn/obs_daily.csv.gz", dtype={"station": str}, parse_dates=["date"])
    df = obs.merge(f, on=["station", "date"])
    st = pd.read_csv(f"{D}/smn/stations.csv", dtype={"station": str})[["station", "lat", "lon", "alt"]]
    ter = pd.read_csv(f"{D}/terrain_stations.csv", dtype={"station": str})
    ter = ter[ter.dem_res_s == 6].drop(columns="dem_res_s")
    df = df.merge(st, on="station").merge(ter, on="station")
    df = add_derived(df)
    df = df.dropna(subset=["tmin", "fc_tmin", "fc_dew", "fc_cloud", "fc_wind", "fc_dtr"])
    df["season"] = np.where(df.date.dt.month >= 7, df.date.dt.year, df.date.dt.year - 1)  # Jul..Jun
    df["dz_grid"] = df.alt - df.grid_elev
    df.to_csv(f"{D}/nights_{model}.csv.gz", index=False)
    print(model, df.shape, "stations", df.station.nunique(), "frost", (df.tmin <= 0).sum())
    print(df.groupby(df.date.dt.to_period("Q")).agg(n=("tmin", "size"), st=("station", "nunique"), frost=("tmin", lambda x: (x <= 0).sum())).to_string())

for m in sys.argv[1:] or ["best_match", "ecmwf_ifs025"]:
    build(m)
