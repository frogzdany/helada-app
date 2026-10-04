"""Extra unseen-site variants: heavily regularised terrain GBM (leaf >= ~5 stations), small-feature ridge offsets, ensembles."""
import sys, os, importlib.util, numpy as np, pandas as pd, lightgbm as lgb
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ex", os.path.join(HERE, "05_experiments.py")); ex = importlib.util.module_from_spec(spec); spec.loader.exec_module(ex)
model = sys.argv[1] if len(sys.argv) > 1 else "best_match"
df = pd.read_csv(f"{ex.D}/nights_{model}.csv.gz", dtype={"station": str}, parse_dates=["date"])
st = df.groupby("station")[["lat", "lon", "alt", "dz_grid"] + ex.TERR[:-1]].first()
SMALL = ["havf_5k", "slope_deg", "tpi_3k", "dz_10k", "horizon_deg", "alt"]
def g(F, **kw):
    p = dict(ex.GBM_P); p.update(kw); return lambda tr, te: lgb.LGBMRegressor(**p).fit(tr[F], tr.tmin).predict(te[F])
V = {
    "gbm_wxonly": g(ex.WEATHER),
    "gbm_terr_reg": g(ex.WEATHER + SMALL, min_child_samples=3000, num_leaves=7),
    "gbm_terr_reg_all": g(ex.WEATHER + ex.TERR + ["dz_grid"], min_child_samples=3000, num_leaves=7),
    "gbm_wx_reg": g(ex.WEATHER + ex.SITE_GATE, min_child_samples=3000, num_leaves=7),
}
stations = np.array(sorted(df.station.unique())); rng = np.random.RandomState(0); rng.shuffle(stations)
folds = np.array_split(stations, 10); parts = []
for S in (2024, 2025):
    w = ex.window(df, S)
    for fi, fs in enumerate(folds):
        te = df[w & df.station.isin(fs)].reset_index(drop=True)
        if len(te) == 0: continue
        tr = df[~w & ~df.station.isin(fs)].reset_index(drop=True)
        P = {k: f(tr, te) for k, f in V.items()}
        parts.append(pd.DataFrame({"station": te.station, "date": te.date, "window": S, "fold": fi, **P}))
o = pd.concat(parts, ignore_index=True)
base = pd.read_csv(f"{ex.OUT}/{model}_U_oos.csv.gz", parse_dates=["date"], dtype={"station": str})
base = base[base.window > 0].merge(o, on=["station", "date", "window", "fold"])
base["ens_wx_rk"] = (base.gbm_wx + base.off_rk) / 2
base["ens_wx_reg"] = (base.gbm_wx + base.gbm_terr_reg) / 2
rows = {k: ex.metrics(base.tmin.values, base[k].values) for k in ["raw", "global_bias", "gbm_wx", "off_rk"] + list(V) + ["ens_wx_rk", "ens_wx_reg"]}
print(pd.DataFrame(rows).T.round(3).to_string())
base.to_csv(f"{ex.OUT}/{model}_U_oos_variants.csv.gz", index=False)
import json
json.dump({k: {kk: round(v[kk], 3) for kk in ("mae", "recall@5", "recall@10", "auc")} for k, v in rows.items() if k not in ("raw", "global_bias", "gbm_wx", "off_rk")},
          open(f"{ex.OUT}/{model}_variants.json", "w"), indent=1)
