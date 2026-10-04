"""Train the production artifacts + the evidence numbers (backtest.json).

Product = two regimes, chosen from research/05_experiments.py + 05b_variants.py:
  station-anchored : GBM(weather + terrain) + the station's own learned offset a_s + b_s*clear_calm
  terrain-transfer : mean of GBM(weather + elev/lat/lon) and a heavily-regularised GBM(weather + 6 terrain features)
Every number in backtest.json is computed with exactly these definitions:
  known_station : forward holdout, train < 2025-10-01, test Oct 2025-Mar 2026 at stations seen in training
  unseen_site   : 10-fold leave-stations-out, test windows Oct-Mar 2024-25 and 2025-26 (train excludes the window)
Calibration (P(frost), 80% interval) = empirical out-of-sample residual distribution per regime, binned by
clear-calm tercile x predicted-Tmin bin. Reliability is checked cross-season (fit on one window, test on the other).

Usage: python research/07_train_product.py [best_match|ecmwf_ifs025]
"""
import gzip, json, os, sys, warnings
import numpy as np, pandas as pd, lightgbm as lgb
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "..", "..", "data")
ART = os.path.join(HERE, "..", "src", "helada_model", "artifacts"); os.makedirs(ART, exist_ok=True)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from helada_model.features import WEATHER
from helada_model import calib

SRC = sys.argv[1] if len(sys.argv) > 1 else "best_match"
TERR_P = ["tpi_300", "tpi_1k", "tpi_3k", "dz_10k", "havf_5k", "relief_5k", "rank_1k", "rank_3k",
          "slope_deg", "northness", "horizon_deg", "elev"]
F_ANCHOR = WEATHER + TERR_P
F_WX = WEATHER + ["elev", "lat", "lon"]
F_REG = WEATHER + ["havf_5k", "slope_deg", "tpi_3k", "dz_10k", "horizon_deg", "elev"]
P_BASE = dict(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=40, reg_lambda=1.0,
              objective="l1", subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=0,
              deterministic=True, force_row_wise=True, n_jobs=4, verbose=-1)
P_REG = dict(P_BASE, num_leaves=7, min_child_samples=3000)


def fit(F, df, p):
    return lgb.LGBMRegressor(**p).fit(df[F], df.tmin)


def offsets(tr, pred, shrink=30.0):
    r = tr.tmin.values - pred; out = {}
    for s, idx in tr.groupby("station").indices.items():
        X = np.c_[np.ones(len(idx)), tr.clear_calm.values[idx]]
        out[s] = np.linalg.solve(X.T @ X + shrink * np.eye(2), X.T @ r[idx])
    return out


def fit_anchor(tr):
    m = fit(F_ANCHOR, tr, P_BASE); return m, offsets(tr, m.predict(tr[F_ANCHOR]))


def pred_anchor(m, off, te):
    ab = np.array([off.get(s, (0.0, 0.0)) for s in te.station])
    return m.predict(te[F_ANCHOR]) + ab[:, 0] + ab[:, 1] * te.clear_calm.values


def fit_transfer(tr):
    return fit(F_WX, tr, P_BASE), fit(F_REG, tr, P_REG)


def pred_transfer(ms, te):
    return 0.5 * (ms[0].predict(te[F_WX]) + ms[1].predict(te[F_REG]))


# ---------------- metrics ----------------
def pofd_recall(y, s, thr):
    a = s <= thr; f = y <= 0
    tp, fp = (a & f).sum(), (a & ~f).sum(); fn, tn = (~a & f).sum(), (~a & ~f).sum()
    return dict(recall=tp / max(tp + fn, 1), pofd=fp / max(fp + tn, 1), precision=tp / max(tp + fp, 1))


def thr_for_pofd(y, s, p):
    nf = np.sort(s[y > 0]); k = int(np.floor(p * len(nf)))
    return -np.inf if k == 0 else nf[k - 1]


def scores(y, p):
    e = p - y; f = y <= 0
    r = dict(mae=float(np.abs(e).mean()), bias=float(e.mean()), mae_frost_nights=float(np.abs(e[f]).mean()))
    for q in (0.05, 0.10):
        pr = pofd_recall(y, p, thr_for_pofd(y, p, q))
        r[f"recall_at_pofd{int(q*100)}"] = float(pr["recall"]); r[f"precision_at_pofd{int(q*100)}"] = float(pr["precision"])
    pr = pofd_recall(y, p, 0.0); r["rule_tmin_le0"] = {k: float(v) for k, v in pr.items()}
    return r


def boot(te, raw, mod, B=500, seed=1):
    y = te.tmin.values; dates = te.date.values; ud = np.unique(dates)
    idx = pd.Series(np.arange(len(y))).groupby(dates).apply(np.array).to_dict()
    rng = np.random.RandomState(seed); dm, d5, d10 = [], [], []
    for _ in range(B):
        ii = np.concatenate([idx[d] for d in rng.choice(ud, len(ud))]); yy, a, b = y[ii], raw[ii], mod[ii]
        dm.append(np.abs(a - yy).mean() - np.abs(b - yy).mean())
        for q, L in ((0.05, d5), (0.10, d10)):
            L.append(pofd_recall(yy, b, thr_for_pofd(yy, b, q))["recall"] - pofd_recall(yy, a, thr_for_pofd(yy, a, q))["recall"])
    ci = lambda L: [round(float(np.percentile(L, 2.5)), 3), round(float(np.percentile(L, 97.5)), 3)]
    return dict(delta_mae_ci=ci(dm), delta_recall_pofd5_ci=ci(d5), delta_recall_pofd10_ci=ci(d10))


def window(df, s):
    return (df.date >= f"{s}-10-01") & (df.date <= f"{s+1}-03-31")


def main():
    df = pd.read_csv(f"{D}/nights_{SRC}.csv.gz", dtype={"station": str}, parse_dates=["date"]).reset_index(drop=True)
    df = df.dropna(subset=F_ANCHOR + ["lat", "lon"]).reset_index(drop=True)

    # ---------- out-of-sample predictions ----------
    K = []
    for S, tr in ((2025, df[df.date < "2025-10-01"]), (2024, df[~window(df, 2024)])):
        te = df[window(df, S) & df.station.isin(tr.station.unique())].reset_index(drop=True)
        m, off = fit_anchor(tr.reset_index(drop=True))
        K.append(te.assign(pred=pred_anchor(m, off, te), win=S))
    K = pd.concat(K, ignore_index=True)
    st = np.array(sorted(df.station.unique())); rng = np.random.RandomState(0); rng.shuffle(st)
    folds = np.array_split(st, 10); U = []
    for S in (2024, 2025):
        w = window(df, S)
        for fi, fs in enumerate(folds):
            te = df[w & df.station.isin(fs)].reset_index(drop=True)
            if te.empty: continue
            ms = fit_transfer(df[~w & ~df.station.isin(fs)].reset_index(drop=True))
            U.append(te.assign(pred=pred_transfer(ms, te), win=S, fold=fi))
    U = pd.concat(U, ignore_index=True)

    # ---------- calibration: cross-season check, then final fit on all OOS ----------
    rep = {"forecast_source": f"Open-Meteo `{SRC}` day-1 forecast (Previous Runs API), station coordinates",
           "target": "SMN observed night Tmin (08:00 manual reading), frost = Tmin <= 0 degC (shelter height)",
           "pofd_note": "recall_at_pofdX = share of frost station-nights caught when each method's alert threshold is set so X% of frost-free station-nights get an alert (ROC-matched, equally fair to raw)",
           "method": {"station-anchored": "LightGBM(weather + 12 terrain features) + station's learned offset a + b*clear_calm",
                      "terrain-transfer": "mean of LightGBM(weather + elev/lat/lon) and regularised LightGBM(weather + 6 terrain features, leaves >= 3000 nights)"}}
    cal_final = {}
    for name, O, seasons, pick in (("known_station", K, ["2025-26"], K.win == 2025),
                                    ("unseen_site", U, ["2024-25", "2025-26"], U.win > 0)):
        # cross-fitted calibration for honest reliability numbers
        probs = np.full(len(O), np.nan); lo = np.full(len(O), np.nan); hi = np.full(len(O), np.nan)
        for S in (2024, 2025):
            fitm, tst = O.win != S, O.win == S
            c = calib.fit(O.pred[fitm].values, O.tmin[fitm].values, O.clear_calm[fitm].values)
            p_, l_, h_ = calib.apply(c, O.pred[tst].values, O.clear_calm[tst].values)
            probs[tst.values], lo[tst.values], hi[tst.values] = p_, l_, h_
        cal_final[name] = calib.fit(O.pred.values, O.tmin.values, O.clear_calm.values)
        T = O[pick].reset_index(drop=True); pr, l, h = probs[pick.values], lo[pick.values], hi[pick.values]
        y = T.tmin.values; f = (y <= 0).astype(float)
        base_rate = float(O[~pick].tmin.le(0).mean()) if (~pick).any() else float(f.mean())
        rel = []
        for a, b in [(0, .05), (.05, .15), (.15, .3), (.3, .5), (.5, .7), (.7, 1.01)]:
            m = (pr >= a) & (pr < b)
            if m.sum(): rel.append(dict(bin=[a, min(b, 1.0)], n=int(m.sum()), p_mean=round(float(pr[m].mean()), 3), obs_freq=round(float(f[m].mean()), 3)))
        alert = (pr >= 0.3) | (l <= 0)
        tp, fp = int((alert & (f == 1)).sum()), int((alert & (f == 0)).sum())
        r = dict(n=int(len(T)), n_frost=int(f.sum()), stations=int(T.station.nunique()), nights=int(T.date.nunique()), seasons=seasons,
                 raw=scores(y, T.fc_tmin.values), model=scores(y, T.pred.values), **boot(T, T.fc_tmin.values, T.pred.values),
                 calibration=dict(method="empirical OOS residual distribution by clear-calm tercile x predicted-Tmin bin; cross-fitted by season",
                                  brier_model=round(float(np.mean((pr - f) ** 2)), 4),
                                  brier_climatology=round(float(np.mean((base_rate - f) ** 2)), 4),
                                  reliability=rel, interval80_coverage=round(float(np.mean((y >= l) & (y <= h))), 3),
                                  interval80_mean_width_c=round(float(np.mean(h - l)), 2)),
                 contract_alert_rule=dict(rule="p_frost >= 0.3 or tmin_lo_c <= 0", recall=round(tp / max(f.sum(), 1), 3),
                                          pofd=round(fp / max((f == 0).sum(), 1), 3), precision=round(tp / max(tp + fp, 1), 3),
                                          alerts_per_100_nights=round(100 * float(alert.mean()), 1)),
                 alternative_rules={f"p_frost >= {t}": dict(recall=round(float(((pr >= t) & (f == 1)).sum() / max(f.sum(), 1)), 3),
                                                            pofd=round(float(((pr >= t) & (f == 0)).sum() / max((f == 0).sum(), 1)), 3),
                                                            alerts_per_100_nights=round(100 * float((pr >= t).mean()), 1))
                                    for t in (0.2, 0.3, 0.5)})
        # same station-nights, compared with Open-Meteo's default blend (what a farmer's weather app shows)
        if SRC != "best_match" and os.path.exists(f"{D}/nights_best_match.csv.gz"):
            bm = pd.read_csv(f"{D}/nights_best_match.csv.gz", dtype={"station": str}, parse_dates=["date"], usecols=["station", "date", "fc_tmin"])
            J = T.merge(bm.rename(columns={"fc_tmin": "fc_bm"}), on=["station", "date"])
            r["vs_open_meteo_default"] = dict(n=int(len(J)), n_frost=int((J.tmin <= 0).sum()), raw_best_match=scores(J.tmin.values, J.fc_bm.values),
                                              model=scores(J.tmin.values, J.pred.values), **boot(J, J.fc_bm.values, J.pred.values, B=300))
        if name == "known_station":
            r["train"] = "all stations, Feb 2024 - Sep 2025 (strict forward)"
        else:
            r["train"] = "10-fold leave-stations-out; train = other stations, all nights outside the test window"
            pf = []
            for fi, g in T.groupby("fold"):
                pf.append(dict(fold=int(fi), stations=int(g.station.nunique()), n=int(len(g)), n_frost=int((g.tmin <= 0).sum()),
                               mae_raw=round(float(np.abs(g.fc_tmin - g.tmin).mean()), 3), mae_model=round(float(np.abs(g.pred - g.tmin).mean()), 3)))
            r["per_fold"] = pf
            for S in (2024, 2025):
                g = T[T.win == S]
                r[f"season_{S}-{(S+1)%100:02d}"] = dict(n=int(len(g)), stations=int(g.station.nunique()),
                                                         mae_raw=round(float(np.abs(g.fc_tmin - g.tmin).mean()), 3),
                                                         mae_model=round(float(np.abs(g.pred - g.tmin).mean()), 3))
        rep[name] = r
        print(name, json.dumps({k: v for k, v in r.items() if k not in ("per_fold",)}, indent=1, default=float)[:3000])

    # research summary table (terrain-transfer question) from research/out
    res_path = os.path.join(HERE, "out", f"{SRC}_results.json")
    if os.path.exists(res_path):
        R = json.load(open(res_path))
        rep["terrain_research_unseen_pooled"] = {k: {kk: round(v[kk], 3) for kk in ("mae", "recall@5", "recall@10", "auc")}
                                                 for k, v in R["U"]["pooled"]["metrics"].items()}
    var_path = os.path.join(HERE, "out", f"{SRC}_variants.json")
    if os.path.exists(var_path):
        rep["terrain_research_unseen_pooled"].update(json.load(open(var_path)))

    # ---------- final fit on everything ----------
    m_a, off = fit_anchor(df); m_wx, m_reg = fit_transfer(df)
    for nm, m in (("model_anchor.txt.gz", m_a), ("model_transfer_wx.txt.gz", m_wx), ("model_transfer_terrain.txt.gz", m_reg)):
        with gzip.open(os.path.join(ART, nm), "wt", compresslevel=9) as fh:
            fh.write(m.booster_.model_to_string())
    stt = df.groupby("station").agg(**{c: (c, "first") for c in ["lat", "lon", "alt"] + TERR_P}, n=("tmin", "size"),
                                     first=("date", "min"), last=("date", "max"))
    names = pd.read_csv(f"{D}/smn/stations.csv", dtype={"station": str}).set_index("station").name
    stations = {s: dict(name=str(names.get(s, "")), **{c: round(float(stt.loc[s, c]), 5 if c in ("lat", "lon") else 3) for c in ["lat", "lon", "alt"] + TERR_P},
                        a=round(float(off[s][0]), 4), b=round(float(off[s][1]), 4), n_nights=int(stt.loc[s, "n"]),
                        period=[stt.loc[s, "first"].strftime("%Y-%m-%d"), stt.loc[s, "last"].strftime("%Y-%m-%d")]) for s in stt.index}
    # reference cold nights (weather only) for climatology offsets
    cold = df[window(df, 2024) | window(df, 2025)]
    cold = cold[cold.fc_tmin <= cold.fc_tmin.quantile(0.25)].drop_duplicates("date").sample(64, random_state=0)
    meta = dict(forecast_source=SRC, features=dict(anchor=F_ANCHOR, transfer_wx=F_WX, transfer_terrain=F_REG),
                anchor_rule=dict(max_km=1.5, max_dz_m=50), trained_on=[df.date.min().strftime("%Y-%m-%d"), df.date.max().strftime("%Y-%m-%d")],
                n_nights=int(len(df)), n_stations=int(df.station.nunique()), calibration=cal_final,
                reference_cold_nights=cold[WEATHER].round(4).to_dict(orient="list"))
    json.dump(dict(meta=meta, stations=stations), open(os.path.join(ART, "model_meta.json"), "w"), separators=(",", ":"), default=float)
    json.dump(rep, open(os.path.join(ART, "backtest.json"), "w"), indent=1, default=float)
    K.to_csv(os.path.join(HERE, "out", f"product_{SRC}_K_oos.csv.gz"), index=False)
    U.to_csv(os.path.join(HERE, "out", f"product_{SRC}_U_oos.csv.gz"), index=False)
    for f in sorted(os.listdir(ART)):
        print(f"{f:36s} {os.path.getsize(os.path.join(ART, f))/1e3:8.1f} KB")


if __name__ == "__main__":
    main()
