"""Print the markdown tables used in README.md from research/out/*.json and artifacts/backtest.json."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__)); O = os.path.join(HERE, "out")
NAMES = {"raw": "Raw day-1 forecast", "global_bias": "Global bias removal", "gbm_wx": "GBM weather + lat/lon/alt (baseline)",
         "gbm_terr": "GBM weather + 12 terrain + lat/lon", "gbm_terr_nolatlon": "GBM weather + 12 terrain",
         "lin_terr": "Ridge, terrain x clear-calm interactions", "off_zero": "Weather-only GBM (no site info)",
         "off_nn": "  + nearest station's offset", "off_idw": "  + IDW of neighbour offsets", "off_gp": "  + kriging (GP) of offsets",
         "off_terr": "  + terrain-regression of offsets", "off_rk": "  + regression-kriging of offsets",
         "gbm_terr_reg": "Regularised terrain GBM (leaf >= 3000 nights)", "ens_wx_reg": "**Ensemble: baseline GBM + regularised terrain GBM**",
         "station_bias": "Per-station bias", "station_linear": "Per-station linear", "anchored": "Weather GBM + station offset",
         "anchored_terr": "**Terrain GBM + station offset**"}
for src in ("best_match", "ecmwf_ifs025"):
    p = f"{O}/{src}_results.json"
    if not os.path.exists(p): continue
    R = json.load(open(p)); V = json.load(open(f"{O}/{src}_variants.json")) if os.path.exists(f"{O}/{src}_variants.json") else {}
    print(f"\n### `{src}` - unseen sites (LSO, pooled 2024-25 + 2025-26): {R['U']['pooled']['info']}\n")
    print("| Method | MAE | Recall @POFD 5% | Recall @POFD 10% | AUC |\n|---|---|---|---|---|")
    M = dict(R["U"]["pooled"]["metrics"])
    for k in ("gbm_terr_reg", "ens_wx_reg"):
        if k in V: M[k] = V[k]
    for k, m in M.items():
        if k in NAMES:
            print(f"| {NAMES[k]} | {m['mae']:.2f} | {100*m['recall@5']:.1f}% | {100*m['recall@10']:.1f}% | {m['auc']:.3f} |")
    print(f"\n### `{src}` - known stations (train < Oct 2025, test Oct 2025-Mar 2026): {R['K']['info']}\n")
    print("| Method | MAE | Recall @POFD 5% | Recall @POFD 10% | AUC |\n|---|---|---|---|---|")
    for k, m in R["K"]["metrics"].items():
        print(f"| {NAMES.get(k, k)} | {m['mae']:.2f} | {100*m['recall@5']:.1f}% | {100*m['recall@10']:.1f}% | {m['auc']:.3f} |")
    pf = R["U"]["per_fold_mae"]
    print(f"\n`{src}` per-fold MAE (unseen, pooled windows)\n\n| Fold | Stations | n | Raw | Baseline GBM | Regression-kriging |\n|---|---|---|---|---|---|")
    for r in pf:
        print(f"| {r['fold']} | {int(r['stations'])} | {int(r['n'])} | {r['raw']:.2f} | {r['gbm_wx']:.2f} | {r['off_rk']:.2f} |")
B = json.load(open(os.path.join(HERE, "..", "src", "helada_model", "artifacts", "backtest.json")))
print("\n### Product backtest (artifacts/backtest.json)\n")
print("| Regime | n (station-nights) | Frost nights | Stations | Seasons | MAE raw -> model | dMAE 95% CI | Recall@5% raw -> model | d [CI] | Recall@10% raw -> model | d [CI] | Brier model / clim | 80% PI coverage (width) |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for k in ("known_station", "unseen_site"):
    r = B[k]; a, m = r["raw"], r["model"]; c = r["calibration"]
    print(f"| {k} | {r['n']} | {r['n_frost']} | {r['stations']} | {', '.join(r['seasons'])} | {a['mae']:.2f} -> {m['mae']:.2f} | {r['delta_mae_ci']} | "
          f"{100*a['recall_at_pofd5']:.1f}% -> {100*m['recall_at_pofd5']:.1f}% | {[round(100*x) for x in r['delta_recall_pofd5_ci']]} | "
          f"{100*a['recall_at_pofd10']:.1f}% -> {100*m['recall_at_pofd10']:.1f}% | {[round(100*x) for x in r['delta_recall_pofd10_ci']]} | "
          f"{c['brier_model']} / {c['brier_climatology']} | {c['interval80_coverage']} ({c['interval80_mean_width_c']} C) |")
for k in ("known_station", "unseen_site"):
    r = B[k]
    print(f"\n{k} contract rule: {r['contract_alert_rule']}; alternatives: {r['alternative_rules']}")
    print(f"{k} reliability: {r['calibration']['reliability']}")
