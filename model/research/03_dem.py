"""Build the regional DEM + station terrain features.

DEM source: Copernicus DEM GLO-30 (ESA / Airbus, (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018,
provided under COPERNICUS by the European Union and ESA), public COGs on AWS Open Data:
  https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N19_00_W100_00_DEM/Copernicus_DSM_COG_10_N19_00_W100_00_DEM.tif
  (registry: https://registry.opendata.aws/copernicus-dem/). INEGI CEM 3.0 (15 m) needs a manual web download, so we did not use it.
Note: GLO-30 is a surface model (DSM): buildings/trees are included.

Usage: python research/03_dem.py <dir with the 6 downloaded tiles>
Writes ../data/dem/dem_{3,6}s.npz (int16) and ../data/terrain_stations.csv.
"""
import os, sys, glob
import numpy as np, pandas as pd, rasterio
from rasterio.merge import merge

HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "..", "..", "data")
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from helada_model.terrain import DEM, point_features, FEATURES

BOUNDS = (-100.4, 18.8, -99.1, 20.2)  # W S E N
src = [rasterio.open(p) for p in sorted(glob.glob(os.path.join(sys.argv[1], "*.tif")))]
arr, tr = merge(src, bounds=BOUNDS, res=1 / 3600)
z = arr[0].astype(np.float32); z[z < -100] = np.nan
print("mosaic", z.shape, tr, np.nanmin(z), np.nanmax(z))
for f in (3, 6):
    h, w = (z.shape[0] // f) * f, (z.shape[1] // f) * f
    zz = np.nanmean(z[:h, :w].reshape(h // f, f, w // f, f), axis=(1, 3))
    np.savez_compressed(f"{D}/dem/dem_{f}s.npz", z=np.round(zz).astype(np.int16), lat0=tr.f, lon0=tr.c, res=f / 3600)
    print(f, zz.shape, os.path.getsize(f"{D}/dem/dem_{f}s.npz") / 1e6, "MB")

from helada_model.terrain import load_dem
st = pd.read_csv(f"{D}/smn/stations.csv", dtype={"station": str})
rows = []
for res in (3, 6):
    dem = load_dem(f"{D}/dem/dem_{res}s.npz")
    for _, s in st.iterrows():
        if not dem.inside(s.lat, s.lon, 0.1): continue
        f = point_features(dem, s.lat, s.lon); f.update(station=s.station, dem_res_s=res); rows.append(f)
t = pd.DataFrame(rows)
t.to_csv(f"{D}/terrain_stations.csv", index=False)
m = t[t.dem_res_s == 3].merge(st[["station", "alt", "name"]], on="station")
print("SMN alt - DEM elev: median abs", (m.alt - m.elev).abs().median(), " >100 m:", ((m.alt - m.elev).abs() > 100).sum())
print(m[FEATURES + ["alt"]].describe().T.to_string())
