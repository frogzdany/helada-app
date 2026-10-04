"""Logger learning curve: how many nights of a parcel logger turn an unseen site into a calibrated one?

Question. The stated precondition is "low-cost loggers on ~5 parcels per ejido extend the validated regime". How many nights
of logger data does a new parcel need before its forecast approaches the station-anchored accuracy?

Simulation (no new data needed). Every SMN station in the unseen-site backtest is a stand-in for a new logger:
  * base prediction = the terrain-transfer product prediction for that station, taken from the 10-fold
    leave-stations-out run of 07_train_product.py (research/out/product_*_U_oos.csv.gz). The station was NEVER in
    the training set of the model that produced it.
  * the logger "reveals" the station's first k observed nights of a frost season (Oct-Mar), k = 0, 7, 14, 30, 60, 90;
    or the whole previous season (2024-25 -> evaluated in 2025-26).
  * from the revealed nights we estimate the site correction r = a + b*clear_calm (r = observed - base) with an
    empirical-Bayes posterior: prior (a, b) ~ N(0, Sigma) centred on the terrain-transfer prediction, Gaussian
    night noise sigma^2 inflated by (1+rho)/(1-rho) for night-to-night autocorrelation. With k = 0 it IS the
    terrain-transfer prediction; with many nights it tends to the station's own offset.
  * Sigma, sigma^2, rho are estimated only from OTHER stations (other CV folds) in the OTHER season. The evaluated
    station contributes nothing but its revealed nights.
Evaluation (strictly after the revealed nights): the common set of nights after the 90th observed night of the
season (roughly Jan-Mar, the core frost months), identical for every k, so the curve is not confounded by season
composition. Metrics: Tmin MAE, frost recall at 5% and 10% POFD (ROC-matched thresholds, frost = Tmin <= 0 degC),
80% interval coverage/width (interval calibration fitted on the other season, cross-fitted like backtest.json).
Baselines on the same nights: k = 0 (unseen-site product) and the station-anchored product (forward/cross-season
OOS predictions of 07_train_product.py, product_*_K_oos) as the ceiling.
CIs: 500 bootstrap resamples of whole nights (dates); plus a station-cluster bootstrap for Delta MAE.

Outputs: research/out/logger_curve_{SRC}.json (full results) and src/helada_model/artifacts/logger_curve.json
(product: EB hyperparameters, per-k interval calibration, measured MAE per k).

Usage: python research/10_logger_learning_curve.py [ecmwf_ifs025]
"""
import json, os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from helada_model import calib  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else "ecmwf_ifs025"
OUT = os.path.join(HERE, "out")
ART = os.path.join(HERE, "..", "src", "helada_model", "artifacts")
KS = [0, 7, 14, 30, 60, 90]
EVAL_FROM = 90          # common evaluation set: observed nights with index >= 90 in the season
MIN_EVAL = 20           # a station-season needs >= 20 evaluation nights
B = 500


# ---------------- empirical Bayes site correction ----------------
def design(cc):
    cc = np.asarray(cc, float)
    return np.c_[np.ones(len(cc)), cc]


def hyper(rows: pd.DataFrame) -> dict:
    """Prior covariance Sigma of (a, b), night noise sigma^2 and lag-1 autocorrelation rho, from full station-seasons."""
    coefs, scov, e_all, lag = [], [], [], []
    for _, g in rows.groupby(["station", "win"]):
        if len(g) < 60:
            continue
        g = g.sort_values("date"); X = design(g.clear_calm); r = g.r.values
        c, *_ = np.linalg.lstsq(X, r, rcond=None); e = r - X @ c
        coefs.append(c); scov.append(np.linalg.inv(X.T @ X)); e_all.append(e)
        dd = g.date.diff().dt.days.values[1:] == 1
        lag.append(np.c_[e[1:][dd], e[:-1][dd]])
    coefs = np.array(coefs); E = np.concatenate(e_all); L = np.concatenate(lag)
    s2 = float(np.var(E)); rho = float(np.clip(np.corrcoef(L[:, 0], L[:, 1])[0, 1], 0.0, 0.9))
    infl = (1 + rho) / (1 - rho)
    S = np.cov(coefs.T) - s2 * infl * np.mean(scov, axis=0)       # between-site covariance minus sampling noise
    w, V = np.linalg.eigh(S); S = (V * np.maximum(w, [0.05, 0.05])) @ V.T
    return dict(Sigma=S.round(5).tolist(), sigma2=round(s2, 4), rho=round(rho, 4),
                n_site_seasons=int(len(coefs)), mean_coef=coefs.mean(0).round(4).tolist())


def posterior(cc, r, hp):
    """Posterior mean and covariance of (a, b) given revealed nights (cc, r). Empty -> prior (0, Sigma)."""
    S = np.asarray(hp["Sigma"]); Si = np.linalg.inv(S)
    if len(r) == 0:
        return np.zeros(2), S
    X = design(cc); w = 1.0 / (hp["sigma2"] * (1 + hp["rho"]) / (1 - hp["rho"]))
    P = w * X.T @ X + Si; C = np.linalg.inv(P)
    return C @ (w * X.T @ np.asarray(r, float)), C


# ---------------- metrics ----------------
def thr_for_pofd(y, s, p):
    nf = np.sort(s[y > 0]); k = int(np.floor(p * len(nf)))
    return -np.inf if k == 0 else nf[k - 1]


def recall_at(y, s, p):
    t = thr_for_pofd(y, s, p); f = y <= 0
    return float(((s <= t) & f).sum() / max(f.sum(), 1))


def metrics(y, p, lo=None, hi=None):
    m = dict(mae=float(np.abs(p - y).mean()), bias=float((p - y).mean()),
             recall5=recall_at(y, p, 0.05), recall10=recall_at(y, p, 0.10))
    if lo is not None:
        m.update(cov80=float(np.mean((y >= lo) & (y <= hi))), width80=float(np.mean(hi - lo)))
    return m


def ci(v):
    return [round(float(np.percentile(v, 2.5)), 3), round(float(np.percentile(v, 97.5)), 3)]


def main():
    U = pd.read_csv(os.path.join(OUT, f"product_{SRC}_U_oos.csv.gz"), dtype={"station": str}, parse_dates=["date"])
    K = pd.read_csv(os.path.join(OUT, f"product_{SRC}_K_oos.csv.gz"), dtype={"station": str}, parse_dates=["date"])
    df = U.merge(K[["station", "date", "pred"]].rename(columns={"pred": "pred_k"}), on=["station", "date"], how="left")
    df = df.rename(columns={"pred": "pred_t"}).sort_values(["station", "win", "date"]).reset_index(drop=True)
    df["r"] = df.tmin - df.pred_t
    df["idx"] = df.groupby(["station", "win"]).cumcount()
    df["n_season"] = df.groupby(["station", "win"]).date.transform("size")
    print(f"{len(df)} station-nights, {df.station.nunique()} stations, pred_k missing: {df.pred_k.isna().sum()}")

    # hyperparameters per (eval window, fold): other folds, other season
    HP = {}
    for w in (2024, 2025):
        for f in sorted(df.fold.unique()):
            HP[(w, f)] = hyper(df[(df.win != w) & (df.fold != f)])
    hp_all = hyper(df)
    print("EB hyper (all):", hp_all)

    # ---------- logger predictions for every k, on ALL nights after the revealed ones ----------
    cols = {}
    for k in KS:
        pk = np.full(len(df), np.nan); pn = np.full(len(df), np.nan); po = np.full(len(df), np.nan)
        for (s, w), g in df.groupby(["station", "win"]):
            hp = HP[(w, int(g.fold.iloc[0]))]
            rev = g[g.idx < k]
            (a, b), _ = posterior(rev.clear_calm.values, rev.r.values, hp)
            ev = g.index[g.idx >= k]
            pk[ev] = df.pred_t.values[ev] + a + b * df.clear_calm.values[ev]
            # ablations: naive mean offset (no shrinkage) and EB offset only
            a_naive = rev.r.mean() if len(rev) else 0.0
            pn[ev] = df.pred_t.values[ev] + a_naive
            S = np.asarray(hp["Sigma"]); w_ = 1.0 / (hp["sigma2"] * (1 + hp["rho"]) / (1 - hp["rho"]))
            a_eb = (w_ * rev.r.sum()) / (w_ * len(rev) + 1.0 / S[0, 0]) if len(rev) else 0.0
            po[ev] = df.pred_t.values[ev] + a_eb
        cols[k] = pk; df[f"p{k}"] = pk; df[f"naive{k}"] = pn; df[f"ebo{k}"] = po

    # previous full season (2024-25 revealed) -> 2025-26, and previous season + first 90 nights of the current one
    df["pprev"] = np.nan; df["pprev90"] = np.nan; prev_n = []
    for s, g in df[df.win == 2025].groupby("station"):
        g0 = df[(df.station == s) & (df.win == 2024)]
        if len(g0) < 60:
            continue
        hp = HP[(2025, int(g.fold.iloc[0]))]
        (a, b), _ = posterior(g0.clear_calm.values, g0.r.values, hp)
        df.loc[g.index, "pprev"] = g.pred_t + a + b * g.clear_calm
        cur = g[g.idx < 90]
        (a2, b2), _ = posterior(np.r_[g0.clear_calm.values, cur.clear_calm.values], np.r_[g0.r.values, cur.r.values], hp)
        ev = g.index[g.idx >= 90]
        df.loc[ev, "pprev90"] = df.pred_t[ev] + a2 + b2 * df.clear_calm[ev]
        prev_n.append(len(g0))

    # ---------- interval calibration, cross-fitted by season ----------
    def xcal(col, fit_rows_mask):
        lo = np.full(len(df), np.nan); hi = np.full(len(df), np.nan); fitted = {}
        for w in (2024, 2025):
            fm = fit_rows_mask & (df.win != w) & df[col].notna()
            if fm.sum() < 500:
                continue
            c = calib.fit(df[col][fm].values, df.tmin[fm].values, df.clear_calm[fm].values); fitted[w] = c
            am = (df.win == w) & df[col].notna()
            _, l, h = calib.apply(c, df[col][am].values, df.clear_calm[am].values)
            lo[am.values], hi[am.values] = l, h
        return lo, hi, fitted

    INT = {}
    for k in KS:
        INT[f"p{k}"] = xcal(f"p{k}", df.idx >= k)[:2]
    INT["pred_k"] = xcal("pred_k", df.pred_k.notna())[:2]
    # previous-season rows only exist in 2025-26: use the within-season k=90 calibration fitted on 2024-25
    c90 = calib.fit(df.p90[(df.win == 2024) & df.p90.notna()].values, df.tmin[(df.win == 2024) & df.p90.notna()].values,
                    df.clear_calm[(df.win == 2024) & df.p90.notna()].values)
    for col in ("pprev", "pprev90"):
        lo = np.full(len(df), np.nan); hi = np.full(len(df), np.nan); m = df[col].notna()
        _, l, h = calib.apply(c90, df[col][m].values, df.clear_calm[m].values); lo[m.values], hi[m.values] = l, h
        INT[col] = (lo, hi)

    # ---------- evaluation on the common set ----------
    E = (df.idx >= EVAL_FROM) & (df.n_season - EVAL_FROM >= MIN_EVAL) & df.pred_k.notna()
    res = dict(source=SRC, eval_set=f"nights with season index >= {EVAL_FROM} (after the 90th observed night), station-seasons with >= {MIN_EVAL} such nights",
               hyper_all=hp_all, ks=KS)

    def table(mask, methods, label):
        T = df[mask].reset_index(drop=True); y = T.tmin.values
        dates = T.date.values; ud = np.unique(dates)
        idx = pd.Series(np.arange(len(T))).groupby(dates).apply(np.array).to_dict()
        rng = np.random.RandomState(1); draws = [np.concatenate([idx[d] for d in rng.choice(ud, len(ud))]) for _ in range(B)]
        sm = {}
        base_col = methods[0][1]
        for name, col in methods:
            p = T[col].values
            lo, hi = (INT[col][0][mask.values], INT[col][1][mask.values]) if col in INT else (None, None)
            m = metrics(y, p, lo, hi)
            bm = np.array([[np.abs(p[ii] - y[ii]).mean(), recall_at(y[ii], p[ii], .05), recall_at(y[ii], p[ii], .10)] for ii in draws])
            b0 = T[base_col].values
            bd = np.array([[np.abs(b0[ii] - y[ii]).mean() - np.abs(p[ii] - y[ii]).mean(),
                            recall_at(y[ii], p[ii], .05) - recall_at(y[ii], b0[ii], .05),
                            recall_at(y[ii], p[ii], .10) - recall_at(y[ii], b0[ii], .10)] for ii in draws])
            m.update(mae_ci=ci(bm[:, 0]), recall5_ci=ci(bm[:, 1]), recall10_ci=ci(bm[:, 2]),
                     d_mae_vs_k0_ci=ci(bd[:, 0]), d_recall5_vs_k0_ci=ci(bd[:, 1]), d_recall10_vs_k0_ci=ci(bd[:, 2]))
            # station-cluster bootstrap of Delta MAE and share of station-seasons improved
            ps = T.assign(e0=np.abs(b0 - y), e1=np.abs(p - y)).groupby(["station", "win"])[["e0", "e1"]].agg(["sum", "count"])
            s0, s1, n_ = ps[("e0", "sum")].values, ps[("e1", "sum")].values, ps[("e0", "count")].values
            rs = np.random.RandomState(2); sb = []
            for _ in range(B):
                j = rs.randint(0, len(n_), len(n_)); sb.append((s0[j].sum() - s1[j].sum()) / n_[j].sum())
            m.update(d_mae_vs_k0_station_ci=ci(sb), share_station_seasons_improved=round(float(np.mean(s1 / n_ < s0 / n_ - 1e-9)), 3),
                     median_station_d_mae=round(float(np.median(s0 / n_ - s1 / n_)), 3))
            sm[name] = {kk: (round(v, 4) if isinstance(v, float) else v) for kk, v in m.items()}
        info = dict(n=int(len(T)), n_frost=int((y <= 0).sum()), stations=int(T.station.nunique()),
                    station_seasons=int(T.groupby(["station", "win"]).ngroups), nights=int(len(ud)))
        print(f"\n== {label}: {info}")
        for name, m in sm.items():
            print(f"  {name:32s} MAE {m['mae']:.2f} {m['mae_ci']}  R5 {m['recall5']:.3f} R10 {m['recall10']:.3f}  "
                  f"cov {m.get('cov80', float('nan')):.2f} w {m.get('width80', float('nan')):.2f}  dMAE_st {m['d_mae_vs_k0_station_ci']} improved {m['share_station_seasons_improved']}")
        return dict(info=info, methods=sm)

    main_methods = [("k=0 (unseen-site product)", "p0")] + [(f"logger k={k}", f"p{k}") for k in KS[1:]] + \
                   [("station-anchored ceiling", "pred_k")]
    res["curve_both_seasons"] = table(E, main_methods, "within-season curve, 2024-25 + 2025-26")
    E25 = E & (df.win == 2025) & df.pprev.notna()
    res["curve_2025_26_with_prev_season"] = table(E25, main_methods[:-1] + [("previous season (2024-25)", "pprev"),
                                                  ("previous season + 90 nights", "pprev90"), ("station-anchored ceiling", "pred_k")],
                                                  "2025-26, stations with a 2024-25 season")
    res["prev_season_nights_median"] = int(np.median(prev_n)) if prev_n else 0
    abl = [("k=0", "p0")] + [(f"{nm} k={k}", f"{c}{k}") for k in (7, 14, 30, 90) for nm, c in
                             (("naive mean offset", "naive"), ("EB offset only", "ebo"), ("EB offset+slope", "p"))]
    res["ablation_shrinkage"] = table(E, abl, "ablation: shrinkage")
    res["ablation_shrinkage_2025_26"] = table(E25, abl, "ablation: shrinkage, 2025-26 subset")
    res["curve_2024_25"] = table(E & (df.win == 2024), main_methods, "within-season curve, 2024-25 only")
    # early-season view: all nights after k (not a common set) -> Delta MAE vs k=0 on those same nights
    early = {}
    for k in KS[1:]:
        m = (df.idx >= k) & df[f"p{k}"].notna()
        early[k] = dict(n=int(m.sum()), mae_k0=round(float(np.abs(df.p0[m] - df.tmin[m]).mean()), 3),
                        mae_k=round(float(np.abs(df[f"p{k}"][m] - df.tmin[m]).mean()), 3))
    res["all_nights_after_k"] = early
    print("\nall nights after k:", early)
    json.dump(res, open(os.path.join(OUT, f"logger_curve_{SRC}.json"), "w"), indent=1, default=float)

    # ---------- product artifact ----------
    C = res["curve_both_seasons"]["methods"]; P = res["curve_2025_26_with_prev_season"]["methods"]
    levels = []
    for k in KS:
        m = df[f"p{k}"].notna() & (df.idx >= k)
        c = calib.fit(df[f"p{k}"][m].values, df.tmin[m].values, df.clear_calm[m].values)
        mm = C["k=0 (unseen-site product)" if k == 0 else f"logger k={k}"]
        levels.append(dict(k=k, mae_c=mm["mae"], recall5=mm["recall5"], recall10=mm["recall10"], width80_c=mm["width80"],
                           cov80=mm["cov80"], calibration=c))
    m = df.pprev.notna()
    kp = res["prev_season_nights_median"]
    levels.append(dict(k=kp, mae_c=P["previous season (2024-25)"]["mae"], recall5=P["previous season (2024-25)"]["recall5"],
                       recall10=P["previous season (2024-25)"]["recall10"], width80_c=P["previous season (2024-25)"]["width80"],
                       cov80=P["previous season (2024-25)"]["cov80"],
                       calibration=calib.fit(df.pprev[m].values, df.tmin[m].values, df.clear_calm[m].values),
                       note="a full previous frost season"))
    art = dict(source=SRC, method="empirical-Bayes site correction r = a + b*clear_calm on top of the terrain-transfer prediction; "
               "prior N(0, Sigma), noise sigma2 inflated by (1+rho)/(1-rho). Levels: measured on held-out SMN stations "
               "(research/10_logger_learning_curve.py); a site with k nights uses the largest level <= k.",
               hyper=hp_all, levels=levels,
               ceiling=dict(mae_c=C["station-anchored ceiling"]["mae"], recall5=C["station-anchored ceiling"]["recall5"]))
    json.dump(art, open(os.path.join(ART, "logger_curve.json"), "w"), separators=(",", ":"), default=float)
    print("artifact logger_curve.json", os.path.getsize(os.path.join(ART, "logger_curve.json")) / 1e3, "KB")


if __name__ == "__main__":
    main()
