"""Research: can terrain close the unseen-site gap?

Regimes
  K  known station, forward temporal holdout: train nights < 2025-10-01 (all stations), test Oct 2025-Mar 2026.
  U  unseen station: 10-fold GroupKFold by station. Test windows Oct-Mar of 2024-25 and 2025-26 at held-out
     stations; train = other stations, all nights OUTSIDE the test window (so no concurrent-night leakage).
     'U-fwd' = 2025-26 window with train strictly before 2025-10-01 (strict forward, fewer test stations).
Methods (see README for detail): raw, global_bias, gbm_wx (baseline: weather + lat/lon/alt), gbm_terr
  (weather + terrain), lin_terr (ridge with terrain x clear-calm interactions), off_* (weather-only base model +
  transferred station offsets a + b*clear_calm via idw / gp kriging / terrain ridge / regression-kriging),
  and for K: station_bias, station_linear, anchored (base + own offsets).
Outputs: research/out/{model}_results.json, oos predictions for calibration.
"""
import os, sys, json, warnings, time
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.linear_model import Ridge, LinearRegression
from sklearn.preprocessing import StandardScaler
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel as C
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "..", "..", "data")
OUT = os.path.join(HERE, "out"); os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from helada_model.features import WEATHER
from helada_model.terrain import FEATURES as TERRAIN

TERR = [f for f in TERRAIN if f != "elev"] + ["alt"]
SITE_GATE = ["alt", "lat", "lon", "dz_grid"]
GBM_P = dict(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=40, reg_lambda=1.0,
             objective="l1", subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=0,
             deterministic=True, force_row_wise=True, n_jobs=4, verbose=-1)
OFF_TERR = ["tpi_300", "tpi_1k", "tpi_3k", "dz_10k", "havf_5k", "relief_5k", "rank_1k", "rank_3k",
            "slope_deg", "northness", "horizon_deg", "alt", "dz_grid"]


def gbm(X, y):
    return lgb.LGBMRegressor(**GBM_P).fit(X, y)


def km_xy(lat, lon):
    return np.c_[(np.asarray(lon) + 99.7) * 111.32 * np.cos(np.radians(19.5)), (np.asarray(lat) - 19.5) * 111.32]


def station_offsets(tr, base_pred, shrink=30.0):
    """Per-station residual model r = a + b*clear_calm (ridge-shrunk toward 0)."""
    r = tr.tmin.values - base_pred
    rows = []
    for s, idx in tr.groupby("station").indices.items():
        X = np.c_[np.ones(len(idx)), tr.clear_calm.values[idx]]
        A = X.T @ X + shrink * np.eye(2); coef = np.linalg.solve(A, X.T @ r[idx])
        rows.append((s, coef[0], coef[1], len(idx)))
    return pd.DataFrame(rows, columns=["station", "a", "b", "n"]).set_index("station")


def transfer(off, st_tr, st_te, how):
    """Predict offsets (a,b) at target stations from training-station offsets."""
    xy_tr, xy_te = km_xy(st_tr.lat, st_tr.lon), km_xy(st_te.lat, st_te.lon)
    Y = off.loc[st_tr.index, ["a", "b"]].values
    if how == "zero":
        return np.zeros((len(st_te), 2))
    if how == "idw":
        d = np.sqrt(((xy_te[:, None, :] - xy_tr[None, :, :]) ** 2).sum(-1))
        out = np.zeros((len(st_te), 2))
        for i in range(len(st_te)):
            j = np.argsort(d[i])[:8]; w = 1 / np.maximum(d[i, j], 1.0) ** 2
            out[i] = (w[:, None] * Y[j]).sum(0) / (w.sum() + 1 / 15.0 ** 2)  # nugget: shrink to 0 when far
        return out
    if how == "nn":
        d = np.sqrt(((xy_te[:, None, :] - xy_tr[None, :, :]) ** 2).sum(-1))
        return Y[d.argmin(1)]
    Xt = StandardScaler().fit(st_tr[OFF_TERR]); A_tr, A_te = Xt.transform(st_tr[OFF_TERR]), Xt.transform(st_te[OFF_TERR])
    if how in ("gp", "rk"):
        out = np.zeros((len(st_te), 2))
        for k in range(2):
            y = Y[:, k].copy(); trend_tr = np.zeros(len(y)); trend_te = np.zeros(len(st_te))
            if how == "rk":
                m = Ridge(alpha=10.0).fit(A_tr, y); trend_tr, trend_te = m.predict(A_tr), m.predict(A_te)
            res = y - trend_tr
            kern = C(1.0, (1e-3, 1e2)) * RBF(10.0, (1.0, 200.0)) + WhiteKernel(1.0, (1e-3, 1e2))
            g = GaussianProcessRegressor(kern, normalize_y=True, random_state=0).fit(xy_tr, res)
            out[:, k] = trend_te + g.predict(xy_te)
        return out
    if how == "terr":
        return np.c_[Ridge(alpha=10.0).fit(A_tr, Y[:, 0]).predict(A_te), Ridge(alpha=10.0).fit(A_tr, Y[:, 1]).predict(A_te)]
    raise ValueError(how)


def lin_terr_design(df, cols_mean=None):
    t = df[TERR].copy()
    if cols_mean is None: cols_mean = (t.mean(), t.std().replace(0, 1))
    z = (t - cols_mean[0]) / cols_mean[1]
    X = pd.concat([df[WEATHER].reset_index(drop=True), z.reset_index(drop=True),
                   (z.mul(df.clear_calm.values, axis=0)).add_suffix("_x_cc").reset_index(drop=True)], axis=1)
    return X, cols_mean


def fit_predict(kind, tr, te, st):
    """st: station table indexed by station with lat, lon + terrain."""
    if kind == "raw":
        return te.fc_tmin.values
    if kind == "global_bias":
        return te.fc_tmin.values - (tr.fc_tmin - tr.tmin).mean()
    if kind == "gbm_wx":
        F = WEATHER + SITE_GATE; return gbm(tr[F], tr.tmin).predict(te[F])
    if kind == "gbm_terr":
        F = WEATHER + TERR + ["lat", "lon", "dz_grid"]; return gbm(tr[F], tr.tmin).predict(te[F])
    if kind == "gbm_terr_nolatlon":
        F = WEATHER + TERR + ["dz_grid"]; return gbm(tr[F], tr.tmin).predict(te[F])
    if kind == "lin_terr":
        X, cm = lin_terr_design(tr); Xe, _ = lin_terr_design(te, cm)
        return Ridge(alpha=1.0).fit(X, tr.tmin).predict(Xe)
    if kind == "station_bias":
        b = (tr.fc_tmin - tr.tmin).groupby(tr.station).mean()
        return te.fc_tmin.values - te.station.map(b).fillna((tr.fc_tmin - tr.tmin).mean()).values
    if kind == "station_linear":
        cols = ["fc_tmin", "fc_cloud", "fc_dep", "fc_wind", "doy_s", "doy_c"]
        glob = LinearRegression().fit(tr[cols], tr.tmin); out = np.full(len(te), np.nan)
        for s, idx in te.groupby("station").indices.items():
            t = tr[tr.station == s]; m = LinearRegression().fit(t[cols], t.tmin) if len(t) > 150 else glob
            out[idx] = m.predict(te.iloc[idx][cols])
        return out
    if kind.startswith("off_") or kind in ("anchored", "anchored_terr"):
        F = WEATHER if kind != "anchored_terr" else WEATHER + TERR + ["dz_grid"]
        m = gbm(tr[F], tr.tmin); off = station_offsets(tr, m.predict(tr[F]))
        base = m.predict(te[F])
        te_st = te.station.unique()
        if kind.startswith("anchored"):
            ab = off.reindex(te_st).fillna(0)[["a", "b"]].values
        else:
            tr_st = off.index.values
            ab = transfer(off, st.loc[tr_st], st.loc[te_st], kind[4:])
        abm = pd.DataFrame(ab, index=te_st, columns=["a", "b"])
        return base + te.station.map(abm.a).values + te.station.map(abm.b).values * te.clear_calm.values
    raise ValueError(kind)


# ---------------- metrics ----------------
def pofd_recall(y, s, thr):
    a = s <= thr; f = y <= 0
    tp, fp = (a & f).sum(), (a & ~f).sum(); fn, tn = (~a & f).sum(), (~a & ~f).sum()
    return dict(recall=tp / max(tp + fn, 1), pofd=fp / max(fp + tn, 1), precision=tp / max(tp + fp, 1))


def thr_for_pofd(y, s, p):
    nf = np.sort(s[y > 0]); k = int(np.floor(p * len(nf)))
    return -np.inf if k == 0 else nf[k - 1]


def metrics(y, p):
    e = p - y; f = y <= 0
    r = dict(n=int(len(y)), n_frost=int(f.sum()), mae=float(np.abs(e).mean()), bias=float(e.mean()),
             mae_frost=float(np.abs(e[f]).mean()) if f.any() else float("nan"),
             auc=float(roc_auc_score(f, -p)) if 0 < f.sum() < len(f) else float("nan"))
    for q in (0.05, 0.10):
        pr = pofd_recall(y, p, thr_for_pofd(y, p, q)); r[f"recall@{int(q*100)}"] = float(pr["recall"])
        r[f"precision@{int(q*100)}"] = float(pr["precision"])
    return r


def boot_delta(te, P, base="raw", B=400, seed=1):
    y = te.tmin.values; dates = te.date.values; ud = np.unique(dates)
    idx_by = pd.Series(np.arange(len(y))).groupby(dates).apply(np.array).to_dict()
    rng = np.random.RandomState(seed); out = {k: dict(dmae=[], d5=[], d10=[], mae=[]) for k in P if k != base}
    for _ in range(B):
        ii = np.concatenate([idx_by[d] for d in rng.choice(ud, len(ud))]); yy = y[ii]; a = P[base][ii]
        ma = np.abs(a - yy).mean()
        ra = {q: pofd_recall(yy, a, thr_for_pofd(yy, a, q))["recall"] for q in (0.05, 0.10)}
        for k in out:
            b = P[k][ii]; mb = np.abs(b - yy).mean()
            out[k]["dmae"].append(ma - mb); out[k]["mae"].append(mb)
            for q, key in ((0.05, "d5"), (0.10, "d10")):
                out[k][key].append(pofd_recall(yy, b, thr_for_pofd(yy, b, q))["recall"] - ra[q])
    ci = lambda L: [float(np.percentile(L, 2.5)), float(np.percentile(L, 97.5))]
    return {k: {kk + "_ci": ci(v) for kk, v in d.items()} for k, d in out.items()}


def window(df, s):
    return (df.date >= f"{s}-10-01") & (df.date <= f"{s+1}-03-31")


def run(model):
    t0 = time.time()
    df = pd.read_csv(f"{D}/nights_{model}.csv.gz", dtype={"station": str}, parse_dates=["date"]).reset_index(drop=True)
    st = df.groupby("station")[["lat", "lon", "alt", "dz_grid"] + TERR[:-1]].first()
    res = {"model": model, "n_stations": int(df.station.nunique()), "n_nights": int(len(df))}

    # ---- K: known stations, strict forward
    K = ["raw", "global_bias", "station_bias", "station_linear", "gbm_wx", "gbm_terr", "anchored", "anchored_terr"]
    tr = df[df.date < "2025-10-01"].reset_index(drop=True); te = df[window(df, 2025)].reset_index(drop=True)
    te = te[te.station.isin(tr.station.unique())].reset_index(drop=True)
    P = {k: fit_predict(k, tr, te, st) for k in K}
    res["K"] = dict(info=dict(n=len(te), stations=int(te.station.nunique()), nights=int(te.date.nunique()),
                              n_frost=int((te.tmin <= 0).sum()), train_n=len(tr), train_stations=int(tr.station.nunique()),
                              test="Oct 2025-Mar 2026", train="Feb 2024-Sep 2025"),
                    metrics={k: metrics(te.tmin.values, p) for k, p in P.items()}, ci=boot_delta(te, P))
    pd.DataFrame({**{"station": te.station, "date": te.date, "tmin": te.tmin, "clear_calm": te.clear_calm}, **P}).to_csv(
        f"{OUT}/{model}_K_oos.csv.gz", index=False)
    print(f"[{model}] K done {time.time()-t0:.0f}s"); print(pd.DataFrame(res["K"]["metrics"]).T.round(3).to_string())

    # ---- U: unseen stations, grouped 10-fold, windows 2024-25 + 2025-26
    U = ["raw", "global_bias", "gbm_wx", "gbm_terr", "gbm_terr_nolatlon", "lin_terr",
         "off_zero", "off_nn", "off_idw", "off_gp", "off_terr", "off_rk"]
    stations = np.array(sorted(df.station.unique())); rng = np.random.RandomState(0); rng.shuffle(stations)
    folds = np.array_split(stations, 10)
    parts = []
    for S in (2024, 2025):
        w = window(df, S)
        for fi, fs in enumerate(folds):
            te = df[w & df.station.isin(fs)].reset_index(drop=True)
            if len(te) == 0: continue
            tr = df[~w & ~df.station.isin(fs)].reset_index(drop=True)
            P = {k: fit_predict(k, tr, te, st) for k in U}
            parts.append(pd.DataFrame({"station": te.station, "date": te.date, "tmin": te.tmin, "clear_calm": te.clear_calm,
                                       "window": S, "fold": fi, **P}))
        print(f"[{model}] U window {S} done {time.time()-t0:.0f}s")
    # strict forward variant for 2025-26
    w = window(df, 2025)
    for fi, fs in enumerate(folds):
        te = df[w & df.station.isin(fs)].reset_index(drop=True)
        if len(te) == 0: continue
        tr = df[(df.date < "2025-10-01") & ~df.station.isin(fs)].reset_index(drop=True)
        P = {k: fit_predict(k, tr, te, st) for k in ["raw", "gbm_wx", "gbm_terr", "off_idw", "off_gp", "off_rk"]}
        parts.append(pd.DataFrame({"station": te.station, "date": te.date, "tmin": te.tmin, "clear_calm": te.clear_calm,
                                   "window": -2025, "fold": fi, **P}))
    oos = pd.concat(parts, ignore_index=True); oos.to_csv(f"{OUT}/{model}_U_oos.csv.gz", index=False)
    res["U"] = {}
    for name, sel in (("pooled", oos.window > 0), ("2024-25", oos.window == 2024), ("2025-26", oos.window == 2025),
                      ("2025-26 strict-forward", oos.window == -2025)):
        o = oos[sel].reset_index(drop=True); ks = [k for k in U if o[k].notna().all()]
        P = {k: o[k].values for k in ks}
        res["U"][name] = dict(info=dict(n=len(o), stations=int(o.station.nunique()), nights=int(o.date.nunique()),
                                        n_frost=int((o.tmin <= 0).sum())),
                              metrics={k: metrics(o.tmin.values, p) for k, p in P.items()},
                              ci=boot_delta(o, P, B=300) if name in ("pooled", "2025-26 strict-forward") else {})
        print(f"--- U {name}", res["U"][name]["info"]); print(pd.DataFrame(res["U"][name]["metrics"]).T.round(3).to_string())
    # per-fold MAE (pooled windows)
    pf = oos[oos.window > 0].groupby("fold").apply(lambda g: pd.Series({k: np.abs(g[k] - g.tmin).mean() for k in U} | {"n": len(g), "stations": g.station.nunique()}))
    res["U"]["per_fold_mae"] = pf.round(3).reset_index().to_dict(orient="records")
    print(pf.round(2).to_string())
    json.dump(res, open(f"{OUT}/{model}_results.json", "w"), indent=1, default=float)
    print(f"[{model}] total {time.time()-t0:.0f}s")


if __name__ == "__main__":
    for m in sys.argv[1:] or ["best_match"]:
        run(m)
