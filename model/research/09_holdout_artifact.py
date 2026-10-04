"""Held-out artifact for honest replays of the 2025-26 season: artifacts/holdout_2025_26/.

Same product definitions as 07_train_product.py, but every model is trained ONLY on nights before 2025-10-01
(Feb/Mar 2024 - Sep 2025), i.e. exactly the "known_station" forward-holdout model of backtest.json:
  station-anchored : GBM(weather + terrain) + station offsets, fitted on nights < 2025-10-01
  terrain-transfer : the two transfer GBMs, fitted on nights < 2025-10-01 (all stations)
  calibration      : fitted on the 2024-25 out-of-sample residuals only (research/out/product_*_{K,U}_oos, win 2024),
                     i.e. the cross-fitted calibration used for the 2025-26 reliability numbers.
Use: helada_model.predict(..., variant="holdout_2025_26"). The app's demo replay (a 2025-26 night) uses it, so the
replay never shows an in-sample result. The shipped (default) artifacts are unchanged.

Usage: python research/09_holdout_artifact.py [ecmwf_ifs025]
"""
import gzip, importlib.util, json, os, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else "ecmwf_ifs025"
sys.argv = [sys.argv[0], SRC]
spec = importlib.util.spec_from_file_location("train", os.path.join(HERE, "07_train_product.py"))
T = importlib.util.module_from_spec(spec); spec.loader.exec_module(T)
from helada_model import calib  # noqa: E402  (07 put ../src on sys.path)

NAME = "holdout_2025_26"
CUT = "2025-10-01"
OUT = os.path.join(T.ART, NAME)


def main():
    os.makedirs(OUT, exist_ok=True)
    df = pd.read_csv(f"{T.D}/nights_{SRC}.csv.gz", dtype={"station": str}, parse_dates=["date"]).reset_index(drop=True)
    df = df.dropna(subset=T.F_ANCHOR + ["lat", "lon"]).reset_index(drop=True)
    tr = df[df.date < CUT].reset_index(drop=True)
    m_a, off = T.fit_anchor(tr)
    m_wx, m_reg = T.fit_transfer(tr)
    for nm, m in (("model_anchor.txt.gz", m_a), ("model_transfer_wx.txt.gz", m_wx), ("model_transfer_terrain.txt.gz", m_reg)):
        with gzip.open(os.path.join(OUT, nm), "wt", compresslevel=9) as fh:
            fh.write(m.booster_.model_to_string())
    K = pd.read_csv(os.path.join(HERE, "out", f"product_{SRC}_K_oos.csv.gz"))
    U = pd.read_csv(os.path.join(HERE, "out", f"product_{SRC}_U_oos.csv.gz"))
    cal = {name: calib.fit(O.pred[O.win == 2024].values, O.tmin[O.win == 2024].values, O.clear_calm[O.win == 2024].values)
           for name, O in (("known_station", K), ("unseen_site", U))}
    stt = tr.groupby("station").agg(**{c: (c, "first") for c in ["lat", "lon", "alt"] + T.TERR_P}, n=("tmin", "size"),
                                     first=("date", "min"), last=("date", "max"))
    names = pd.read_csv(f"{T.D}/smn/stations.csv", dtype={"station": str}).set_index("station").name
    stations = {s: dict(name=str(names.get(s, "")), **{c: round(float(stt.loc[s, c]), 5 if c in ("lat", "lon") else 3)
                                                       for c in ["lat", "lon", "alt"] + T.TERR_P},
                        a=round(float(off[s][0]), 4), b=round(float(off[s][1]), 4), n_nights=int(stt.loc[s, "n"]),
                        period=[stt.loc[s, "first"].strftime("%Y-%m-%d"), stt.loc[s, "last"].strftime("%Y-%m-%d")])
                for s in stt.index}
    meta = dict(forecast_source=SRC, variant=NAME,
                note="trained only on nights before 2025-10-01; calibration from 2024-25 out-of-sample residuals. "
                     "For replaying 2025-26 nights without in-sample leakage.",
                features=dict(anchor=T.F_ANCHOR, transfer_wx=T.F_WX, transfer_terrain=T.F_REG),
                anchor_rule=dict(max_km=1.5, max_dz_m=50),
                trained_on=[tr.date.min().strftime("%Y-%m-%d"), tr.date.max().strftime("%Y-%m-%d")],
                n_nights=int(len(tr)), n_stations=int(tr.station.nunique()), calibration=cal)
    json.dump(dict(meta=meta, stations=stations), open(os.path.join(OUT, "model_meta.json"), "w"),
              separators=(",", ":"), default=float)
    for f in sorted(os.listdir(OUT)):
        print(f"{NAME}/{f:34s} {os.path.getsize(os.path.join(OUT, f))/1e3:8.1f} KB")


if __name__ == "__main__":
    main()
