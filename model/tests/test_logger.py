"""Parcel loggers: calibrate_site() + predict(..., sites=...) + the measured learning curve."""
import json

import numpy as np
import pandas as pd
import pytest

import helada_model as hm
from helada_model import Parcel, logger as lg

FAR = Parcel("p-far", 19.60, -99.88)                   # terrain-transfer (between stations)
TOLUCA_OBS = Parcel("p-obs", 19.29111, -99.71417)      # station-anchored


def nights(n, start="2025-10-01", seed=0):
    rng = np.random.RandomState(seed)
    out = {}
    for i, d in enumerate(pd.date_range(start, periods=n)):
        out[d.strftime("%Y-%m-%d")] = dict(tmin_c=float(4 + 3 * np.sin(i / 5)), dew_c=float(-4 + rng.randn()),
                                           cloud_pct=float(rng.uniform(0, 90)), wind_kmh=float(rng.uniform(2, 15)),
                                           tmax_prev_c=21.0)
    return out


def transfer_pred(p, d, f):
    return hm.predict([p], d, forecast=f)[0]


def fake_logger(p, fx, offset):
    return [(d, round(transfer_pred(p, d, f).tmin_c + offset, 2)) for d, f in fx.items()]


def test_zero_nights_is_terrain_transfer():
    fx = nights(3)
    sc = hm.calibrate_site(FAR, [], forecasts=fx)
    assert sc.n_nights == 0 and sc.a_c == 0 and sc.b_c == 0 and sc.level_k == 0
    a = hm.predict([FAR], "2026-01-10", forecast=fx["2025-10-01"])[0]
    b = hm.predict([FAR], "2026-01-10", forecast=fx["2025-10-01"], sites={"p-far": sc})[0]
    assert a.tmin_c == b.tmin_c and hm.support_of(b) == "terrain-transfer"


def test_logger_shifts_prediction_narrows_interval_and_labels_support():
    fx = nights(60)
    sc = hm.calibrate_site(FAR, fake_logger(FAR, fx, +3.0), forecasts=fx)
    assert sc.n_nights == 60 and sc.support == "logger-anchored (60 noches)" and sc.level_k == 60
    assert 2.0 < sc.a_c + sc.b_c * 0.3 < 3.2                   # shrunk a little toward 0, not overfit past +3
    f = {"tmin_c": 2.5, "dew_c": -6.0, "cloud_pct": 5.0, "wind_kmh": 4.0, "tmax_prev_c": 21.0}
    t = hm.predict([FAR], "2026-01-10", forecast=f)[0]
    g = hm.predict([FAR], "2026-01-10", forecast=f, sites=[sc])[0]
    assert hm.support_of(g) == "logger-anchored (60 noches)"
    assert g.drivers["support_logger_anchored"] == 1.0 and g.drivers["logger_nights"] == 60.0
    assert g.tmin_c == pytest.approx(t.tmin_c + g.drivers["logger_offset_c"], abs=0.02)
    assert g.tmin_c > t.tmin_c + 1.5 and g.p_frost < t.p_frost
    assert (g.tmin_hi_c - g.tmin_lo_c) < (t.tmin_hi_c - t.tmin_lo_c)
    assert g.drivers["logger_expected_mae_c"] < 2.0


def test_shrinkage_grows_with_nights():
    fx = nights(30)
    obs = fake_logger(FAR, fx, -4.0)
    est = [hm.calibrate_site(FAR, obs[:k], forecasts=fx) for k in (3, 7, 30)]
    offs = [s.a_c + s.b_c * 0.4 for s in est]
    assert -4.0 < offs[2] < offs[1] < offs[0] < 0          # few nights: pulled toward the terrain-transfer prediction
    assert offs[0] > -2.5


def test_only_nights_before_the_forecast_night_are_used():
    fx = nights(40, start="2025-11-01")
    sc = hm.calibrate_site(FAR, fake_logger(FAR, fx, +3.0), forecasts=fx)
    f = fx["2025-11-20"]
    early = hm.predict([FAR], "2025-11-01", forecast=f, sites=[sc])[0]     # no logger night before Nov 1
    assert hm.support_of(early) == "terrain-transfer"
    mid = hm.predict([FAR], "2025-11-20", forecast=f, sites=[sc])[0]       # Nov 1..19 only
    assert hm.support_of(mid) == "logger-anchored (19 noches)"
    assert mid.drivers["logger_nights"] == 19.0


def test_qc_morning_dates_duplicates_and_missing_forecasts():
    fx = nights(5, start="2025-12-01")
    obs = [("2025-12-02", 1.0), ("2025-12-02", 0.5),       # morning of Dec 2 -> night of Dec 1; later reading wins
           ("2025-12-03", 99.0), ("no-es-fecha", 1.0), ("2025-12-04", "x"), ("2025-12-30", -2.0)]
    sc = hm.calibrate_site(FAR, obs, forecasts=fx, date_is="morning")
    assert sc.n_nights == 1 and sc.nights[0]["date"] == "2025-12-01" and sc.nights[0]["tmin_c"] == 0.5
    reasons = " | ".join(r["reason"] for r in sc.rejected)
    assert "fuera de rango" in reasons and "fecha no válida" in reasons and "no numérica" in reasons
    assert "sin pronóstico archivado" in reasons              # Dec 29 night: no forecast given (and no fetch)


def test_roundtrip_json_and_variant_refit():
    fx = nights(14)
    sc = hm.calibrate_site(FAR, fake_logger(FAR, fx, 1.0), forecasts=fx)
    d = json.loads(json.dumps(sc.to_dict()))
    sc2 = hm.SiteCalibration.from_dict(d)
    assert sc2 == sc
    f = fx["2025-10-05"]
    a = hm.predict([FAR], "2026-01-10", forecast=f, sites=[sc2])[0]
    b = hm.predict([FAR], "2026-01-10", forecast=f, sites=[sc2], variant="holdout_2025_26")[0]
    assert hm.support_of(a) == hm.support_of(b) == "logger-anchored (14 noches)"


def test_station_anchored_parcel_keeps_station_regime():
    fx = nights(30)
    sc = hm.calibrate_site(TOLUCA_OBS, [(d, 5.0) for d in fx], forecasts=fx)
    f = hm.predict([TOLUCA_OBS], "2026-01-10", forecast=fx["2025-10-01"], sites=[sc])[0]
    assert hm.support_of(f) == "station-anchored" and f.drivers["support_logger_anchored"] == 0.0


def test_measured_curve_is_monotone_and_below_unseen():
    c = lg.curve()
    ks = [lv["k"] for lv in c["levels"]]
    assert ks[:6] == [0, 7, 14, 30, 60, 90] and ks[-1] >= 150
    maes = [hm.expected_precision(k)["mae_c"] for k in (0, 7, 14, 30, 60)]
    assert all(a > b for a, b in zip(maes, maes[1:]))
    assert maes[0] > 2.0 and hm.expected_precision(200)["mae_c"] < 1.7
    assert hm.expected_precision(45)["level_k"] == 30          # conservative: largest measured level <= k
    for lv in c["levels"]:
        assert 0.70 <= lv["cov80"] <= 0.90


def test_parse_archive_payload():
    from helada_model import forecast as fc
    t = pd.date_range("2025-11-14 00:00", "2025-11-16 23:00", freq="h")
    n = len(t)
    j = {"elevation": 2650.0, "hourly": {
        "time": [x.strftime("%Y-%m-%dT%H:%M") for x in t],
        "temperature_2m_previous_day1": list(np.r_[np.full(n // 2, 10.0), np.full(n - n // 2, 5.0)]),
        "dew_point_2m_previous_day1": [0.0] * n, "cloud_cover_previous_day1": [10.0] * n,
        "wind_speed_10m_previous_day1": [5.0] * n}}
    out = fc.parse_archive(j)
    assert "2025-11-14" in out and out["2025-11-14"]["grid_elev_m"] == 2650.0
    assert out["2025-11-14"]["tmin_c"] == 10.0 and out["2025-11-15"]["tmin_c"] == 5.0
