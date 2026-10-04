"""Probability + interval calibration from out-of-sample residuals.

For each regime we keep the empirical distribution of out-of-sample residuals r = observed - predicted Tmin,
binned by clear-calm tercile x predicted-Tmin bin (radiative nights and cold predictions carry bigger, skewed errors).
  P(frost) = P(pred + r <= 0) = ECDF_bin(-pred)
  80% interval = pred + [q10, q90]
then P(frost) is passed through an isotonic map fitted on the same out-of-sample data (monotone recalibration).
Because the residuals come from held-out stations (terrain-transfer) or a held-out season (station-anchored),
the unseen-site intervals are automatically wider: they carry the measured unseen-site error.
"""
from __future__ import annotations

import numpy as np

QS = np.round(np.linspace(0.005, 0.995, 100), 4)
PRED_EDGES = [-np.inf, 1.0, 4.0, 7.0, np.inf]
MIN_N = 150
ISO_BIN = 300


def fit(pred, y, cc) -> dict:
    pred, y, cc = map(lambda a: np.asarray(a, float), (pred, y, cc))
    r = y - pred
    cc_edges = [-np.inf, float(np.quantile(cc, 1 / 3)), float(np.quantile(cc, 2 / 3)), np.inf]
    bins = {}
    for i in range(3):
        mc = (cc > cc_edges[i]) & (cc <= cc_edges[i + 1])
        for j in range(len(PRED_EDGES) - 1):
            m = mc & (pred > PRED_EDGES[j]) & (pred <= PRED_EDGES[j + 1])
            src = r[m] if m.sum() >= MIN_N else r[mc]
            bins[f"{i}_{j}"] = dict(n=int(m.sum()), q=np.round(np.quantile(src, QS), 3).tolist())
    c = dict(cc_edges=[float(x) for x in cc_edges], pred_edges=[float(x) for x in PRED_EDGES],
             qs=QS.tolist(), bins=bins, iso=None)
    # isotonic recalibration of the residual-ECDF probability (fixes mid-range under-confidence seen cross-season)
    from sklearn.isotonic import IsotonicRegression
    # Fitted on bins of >= ISO_BIN nights (robust tails), anchored at (0,0) and (1,1), linear in between.
    p_raw = apply(c, pred, cc)[0]
    o = np.argsort(p_raw, kind="stable"); ps, fs = p_raw[o], (y[o] <= 0).astype(float)
    nb = max(len(ps) // ISO_BIN, 2); parts = np.array_split(np.arange(len(ps)), nb)
    bx = np.array([ps[i].mean() for i in parts]); by = np.array([fs[i].mean() for i in parts]); bw = np.array([len(i) for i in parts])
    iy = IsotonicRegression(y_min=0.0, y_max=1.0).fit(bx, by, sample_weight=bw).predict(bx)
    x = np.r_[0.0, bx, 1.0]; yy = np.maximum.accumulate(np.r_[0.0, iy, 1.0])
    c["iso"] = dict(x=np.round(x, 4).tolist(), y=np.round(yy, 4).tolist())
    return c


def _bin(c, p, k):
    ce, pe = c["cc_edges"], c["pred_edges"]
    i = int(np.clip(np.searchsorted(ce, k, side="left") - 1, 0, 2))
    j = int(np.clip(np.searchsorted(pe, p, side="left") - 1, 0, len(pe) - 2))
    return c["bins"][f"{i}_{j}"]["q"]


def apply(c, pred, cc):
    pred, cc = np.atleast_1d(np.asarray(pred, float)), np.atleast_1d(np.asarray(cc, float))
    qs = np.asarray(c["qs"])
    p = np.empty(len(pred)); lo = np.empty(len(pred)); hi = np.empty(len(pred))
    for n, (x, k) in enumerate(zip(pred, cc)):
        q = np.asarray(_bin(c, x, k))
        # ECDF of residuals at -x, linearly interpolated on the quantile grid, clipped to [0.005, 0.995]
        p[n] = float(np.interp(-x, q, qs, left=0.0, right=1.0))
        lo[n] = x + float(np.interp(0.10, qs, q)); hi[n] = x + float(np.interp(0.90, qs, q))
    if c.get("iso"):
        p = np.interp(p, c["iso"]["x"], c["iso"]["y"])
    p = np.clip(p, 0.001, 0.995)
    return p, lo, hi
