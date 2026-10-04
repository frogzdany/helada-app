"""The demo scenario is a replay of a real held-out night (2025-11-14 -> 15), run through the REAL helada_model."""
from datetime import datetime
from zoneinfo import ZoneInfo

from backend import alerts as A
from backend import scenarios

TZ = ZoneInfo("America/Mexico_City")


def rows_by_id(fc):
    return {r["parcel"]["parcel_id"]: r for r in fc["rows"]}


def test_real_model_catches_warm_forecast_frost(svc, real_model):
    fc = svc.forecast(None, "demo")
    assert fc["model_source"] == "helada_model" and fc["date"] == "2025-11-14"
    rows = rows_by_id(fc)
    p01 = rows["P01"]
    f, t = p01["forecast"], p01["truth"]
    # the archived ECMWF forecast said well above freezing; the SMN station next door observed frost
    assert f["grid_tmin_c"] > 1.0 and t["observed_tmin_c"] <= 0.0
    assert f["support"] == "station-anchored" and f["drivers"]["forecast_fallback"] == 0
    # held-out variant (trained without 2025-26): reproduces the backtest's out-of-sample numbers, no leakage
    assert fc["model_variant"] == "holdout_2025_26" and "sin la temporada 2025-26" in fc["model_variant_label"]
    assert f["drivers"]["variant_holdout_2025_26"] == 1.0
    assert abs(f["tmin_c"] - 0.48) < 0.05 and abs(f["p_frost"] - 0.541) < 0.02 and f["p_frost"] >= 0.3
    # every replayed "warm forecast, observed frost" night that the held-out backtest caught is caught here too
    hits = [pid for pid, r in rows.items() if r["truth"] and r["truth"]["holdout"]
            and r["truth"]["holdout"]["outcome"] == "hit" and r["forecast"]["grid_tmin_c"] > 1.0]
    assert len(hits) >= 4
    for pid in hits:
        assert rows[pid]["truth"]["observed_tmin_c"] <= 0 and rows[pid]["forecast"]["p_frost"] >= 0.3, pid
    # the app now shows exactly the held-out numbers, including the honest miss and false alarm
    for pid, r in rows.items():
        h = r["truth"] and r["truth"]["holdout"]
        if h:
            assert abs(r["forecast"]["p_frost"] - h["p_frost"]) < 0.02, pid
    assert rows["P06"]["forecast"]["p_frost"] < 0.3 and rows["P06"]["truth"]["observed_tmin_c"] <= 0   # miss
    assert rows["P09"]["forecast"]["p_frost"] >= 0.3 and rows["P09"]["truth"]["observed_tmin_c"] > 0   # false alarm
    levels = {pid: r["level"] for pid, r in rows.items()}
    assert sorted(p for p, lv in levels.items() if lv == "alert") == ["P01", "P02", "P03", "P07", "P08", "P09"]
    assert [p for p, lv in levels.items() if lv == "watch"] == ["P10"]


def test_live_scenario_uses_shipped_model(svc, real_model, monkeypatch):
    from backend import model_adapter
    monkeypatch.setattr(model_adapter, "fetch_grid_forecast", lambda parcels, d, **k: scenarios.demo_forecast(parcels))
    fc = svc.forecast("2025-11-14")
    assert fc["model_variant"] is None
    assert all(r["forecast"]["drivers"]["variant_holdout_2025_26"] == 0.0 for r in fc["rows"])


def test_terrain_transfer_parcels_show_wide_band_and_logger_message(svc, real_model):
    rows = rows_by_id(svc.forecast(None, "demo"))
    anch = [r["forecast"] for r in rows.values() if r["forecast"]["support"] == "station-anchored"]
    tran = [r["forecast"] for r in rows.values() if r["forecast"]["support"] == "terrain-transfer"]
    assert len(anch) == 10 and {f["parcel_id"] for f in tran} == {"P05", "P10"}
    width = lambda fs: sum(f["tmin_hi_c"] - f["tmin_lo_c"] for f in fs) / len(fs)
    assert width(tran) > width(anch) + 1.0
    msg = A.render(rows["P10"]["parcel"], rows["P10"]["forecast"], datetime(2025, 11, 14, 18, 30, tzinfo=TZ))
    assert "sin estación cercana" in msg["detail"] and "registrador" in msg["detail"]
    assert  " aprox" in msg["sms"] and len(msg["sms"]) <= 160
    msg = A.render(rows["P01"]["parcel"], rows["P01"]["forecast"], datetime(2025, 11, 14, 18, 30, tzinfo=TZ))
    assert "Base: estación SMN Tres Barrancas" in msg["detail"] and len(msg["text"].split("\n")) <= 4


def test_fixture_is_honest_about_counterexamples():
    fx = scenarios.replay()
    assert fx["night_date"] == "2025-11-14" and "real" in fx["label"]
    out = {pid: e["holdout"]["outcome"] for pid, e in fx["parcels"].items() if e["holdout"]}
    assert out["P01"] == "hit" and fx["parcels"]["P01"]["holdout"]["p_frost"] >= 0.3   # held-out model, not in-sample
    assert out["P06"] == "miss"            # Toluca obs.: forecast +8.9, observed -1.1, held-out P = 19%
    assert out["P09"] == "false_alarm"     # Santa María del Llano: held-out P = 65%, observed +3.0
    ns = fx["night_summary"]
    assert ns["frost_with_forecast_above_1c"] == 12 and ns["caught_p_ge_0_3"] == 7
    for e in fx["parcels"].values():
        f = e["forecast"]
        assert {"grid_tmin_c", "dew_c", "cloud_cover", "wind_kmh", "tmax_prev_c", "grid_elev_m"} <= set(f)
        assert "ecmwf_ifs025" in f["source"]
