import dataclasses
import json
import math
import os

import numpy as np
import pytest

import helada_model as hm
from helada_model import FrostClimatology, FrostForecast, Parcel

ART = os.path.join(os.path.dirname(hm.__file__), "artifacts")

# Real coordinates in the Toluca valley (synthetic owners)
TOLUCA_OBS = Parcel("p-obs", 19.29111, -99.71417)            # SMN 15126 site -> station-anchored
ATOTONILCO = Parcel("p-ato", 19.46222, -99.77556)            # SMN 15010, cold valley-floor station
FAR = Parcel("p-far", 19.60, -99.88, elev_m=None)            # between stations -> terrain-transfer
FC_CLEAR = {"tmin_c": 2.5, "dew_c": -6.0, "cloud_pct": 5.0, "wind_kmh": 4.0, "tmax_prev_c": 21.0}
FC_CLOUDY = {"tmin_c": 2.5, "dew_c": 1.5, "cloud_pct": 95.0, "wind_kmh": 18.0, "tmax_prev_c": 12.0}


def test_interface_types():
    out = hm.predict([TOLUCA_OBS, FAR], "2026-01-10", forecast=FC_CLEAR)
    assert len(out) == 2
    for f in out:
        assert isinstance(f, FrostForecast)
        assert {x.name for x in dataclasses.fields(FrostForecast)} == {
            "parcel_id", "date", "grid_tmin_c", "tmin_c", "tmin_lo_c", "tmin_hi_c", "p_frost", "drivers"}
        assert f.date == "2026-01-10"
        assert f.tmin_lo_c <= f.tmin_c <= f.tmin_hi_c
        assert 0.0 <= f.p_frost <= 1.0
        assert isinstance(f.drivers, dict) and all(isinstance(v, float) for v in f.drivers.values())
        assert f.grid_tmin_c == pytest.approx(2.5)
    assert out[0].parcel_id == "p-obs" and out[1].parcel_id == "p-far"


def test_support_regimes_and_wider_transfer_interval():
    a, t = hm.predict([TOLUCA_OBS, FAR], "2026-01-10", forecast=FC_CLEAR)
    assert a.drivers["support_station_anchored"] == 1.0
    assert t.drivers["support_station_anchored"] == 0.0
    assert hm.support_of(a) == "station-anchored" and hm.support_of(t) == "terrain-transfer"
    assert (t.tmin_hi_c - t.tmin_lo_c) > (a.tmin_hi_c - a.tmin_lo_c)


def test_per_parcel_forecast_dict_and_elev_fill():
    fc = {"p-obs": FC_CLEAR, "p-far": FC_CLOUDY}
    a, t = hm.predict([TOLUCA_OBS, FAR], "2026-01-10", forecast=fc)
    assert t.drivers["clear_calm"] < a.drivers["clear_calm"]
    assert 2000 < t.drivers["elev_m"] < 4000   # filled from the DEM


def test_deterministic():
    p = [TOLUCA_OBS, ATOTONILCO, FAR]
    a = hm.predict(p, "2025-12-20", forecast=FC_CLEAR)
    b = hm.predict(p, "2025-12-20", forecast=FC_CLEAR)
    assert [dataclasses.asdict(x) for x in a] == [dataclasses.asdict(x) for x in b]


def test_physical_sanity():
    # colder forecast -> colder parcel Tmin and higher P(frost)
    warm = hm.predict([ATOTONILCO], "2026-01-10", forecast={**FC_CLEAR, "tmin_c": 6.0})[0]
    cold = hm.predict([ATOTONILCO], "2026-01-10", forecast={**FC_CLEAR, "tmin_c": -1.0})[0]
    assert cold.tmin_c < warm.tmin_c and cold.p_frost > warm.p_frost
    # Atotonilco is a known cold-pooling site: the model should be colder than the raw grid on a clear, calm night
    assert cold.tmin_c < -1.0


def test_calibration_sanity():
    rep = hm.backtest_report()
    for regime in ("known_station", "unseen_site"):
        cal = rep[regime]["calibration"]
        assert cal["brier_model"] < cal["brier_climatology"]
        # reliability: in bins with enough nights the observed frequency is within 0.15 of the forecast probability
        for b in cal["reliability"]:
            if b["n"] >= 200:
                assert abs(b["obs_freq"] - b["p_mean"]) < 0.15, b
        assert 0.6 <= cal["interval80_coverage"] <= 0.95


def test_backtest_report_shape():
    rep = hm.backtest_report()
    for regime in ("known_station", "unseen_site"):
        r = rep[regime]
        for k in ("n", "n_frost", "stations", "seasons", "raw", "model", "delta_mae_ci"):
            assert k in r, (regime, k)
        assert r["model"]["mae"] < r["raw"]["mae"]
    json.dumps(rep)


def test_climatology():
    c = hm.climatology(ATOTONILCO)
    assert isinstance(c, FrostClimatology)
    assert 0 <= c.last_spring_frost_doy_p50 <= c.last_spring_frost_doy_p90 <= 200
    assert 180 <= c.first_autumn_frost_doy_p10 <= c.first_autumn_frost_doy_p50 <= 366
    assert 0 < c.frost_free_days_p50 <= 366
    far = hm.climatology(FAR)
    assert isinstance(far.frost_free_days_p50, int)


def test_artifact_size():
    model_files = [f for f in os.listdir(ART) if f.startswith("model")]
    assert model_files
    total = sum(os.path.getsize(os.path.join(ART, f)) for f in model_files)
    assert total < 1_000_000, total
    everything = sum(os.path.getsize(os.path.join(ART, f)) for f in os.listdir(ART))
    assert everything < 2_000_000, everything


def test_offline_without_forecast_raises_or_fetches(monkeypatch):
    import helada_model.forecast as fc

    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(fc, "fetch_night", boom)
    with pytest.raises(OSError):
        hm.predict([TOLUCA_OBS], "2026-01-10", forecast=None)


def test_app_shaped_forecast_and_support_of_parcel():
    # the shape the app's demo scenario passes: grid Tmin, cloud %, wind in m/s, RH %, grid elevation (may be None)
    fc = {"p-obs": {"grid_tmin_c": 3.0, "cloud_cover": 5.0, "wind_ms": 1.0, "rh": 60.0, "grid_elev_m": None, "source": "demo"},
          "p-far": {"grid_tmin_c": 3.0, "cloud_cover": 5.0, "wind_ms": 1.0, "rh": 60.0, "grid_elev_m": 2400.0}}
    a, t = hm.predict([TOLUCA_OBS, FAR], "2026-10-03", forecast=fc)
    assert a.grid_tmin_c == pytest.approx(3.0) and t.grid_tmin_c == pytest.approx(3.0)
    assert a.drivers["forecast_fallback"] == 1.0            # only tmax_prev missing
    assert a.drivers["wind_kmh"] == pytest.approx(3.6)
    assert a.drivers["lapse_adj_c"] == 0.0 and t.drivers["lapse_adj_c"] < 0   # parcel above the 2400 m grid cell
    assert hm.support_of(TOLUCA_OBS) == "station-anchored" and hm.support_of(FAR) == "terrain-transfer"
    json.dumps(dataclasses.asdict(hm.climatology(FAR)))


def test_holdout_variant_reproduces_backtest_and_is_small():
    """variant='holdout_2025_26' = trained without the 2025-26 season; replays a 2025-26 night out of sample.
    SMN 15282 Tres Barrancas, night 2025-11-14: archived ecmwf_ifs025 forecast +9.2 degC, observed -3.0 degC;
    the backtest's held-out prediction was 0.48 degC, P(frost) 0.541."""
    p = Parcel("tb", 19.3351, -99.7923)
    fc = {"tmin_c": 9.2, "dew_c": 0.29, "cloud_pct": 10.7, "wind_kmh": 2.73, "tmax_prev_c": 21.5}
    [h] = hm.predict([p], "2025-11-14", forecast=fc, variant="holdout_2025_26")
    [s] = hm.predict([p], "2025-11-14", forecast=fc)
    assert h.drivers["support_station_anchored"] == 1.0 and h.drivers["variant_holdout_2025_26"] == 1.0
    assert s.drivers["variant_holdout_2025_26"] == 0.0
    assert h.tmin_c == pytest.approx(0.48, abs=0.05) and h.p_frost == pytest.approx(0.541, abs=0.02)
    assert h.tmin_c != s.tmin_c
    with pytest.raises(ValueError):
        hm.predict([p], "2025-11-14", forecast=fc, variant="nope")
    d = os.path.join(ART, "holdout_2025_26")
    assert sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d) if f.endswith(".gz")) < 1_000_000


def test_fetch_nights_batch_parser(monkeypatch):
    """Batch Open-Meteo response (list of location objects, model-suffixed columns) parses per location."""
    import io
    import json as _json

    import pandas as pd

    from helada_model import forecast as fc
    times = pd.date_range("2026-01-10 00:00", "2026-01-11 23:00", freq="h").strftime("%Y-%m-%dT%H:%M").tolist()

    def loc(t0, elev):
        return {"elevation": elev, "hourly": {
            "time": times,
            "temperature_2m_ecmwf_ifs025": [t0 + (5 if 10 <= i % 24 <= 17 else 0) for i in range(len(times))],
            "dew_point_2m_ecmwf_ifs025": [-3.0] * len(times),
            "cloud_cover_ecmwf_ifs025": [10.0] * len(times),
            "wind_speed_10m_ecmwf_ifs025": [4.0] * len(times)}}
    payload = _json.dumps([loc(1.0, 2700.0), loc(-2.0, 2800.0)]).encode()
    seen = {}

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake(url, timeout=None):
        seen["url"] = url
        return R(payload)
    monkeypatch.setattr(fc.urllib.request, "urlopen", fake)
    out = fc.fetch_nights([(19.3, -99.7), (19.4, -99.8)], "2026-01-10", model="ecmwf_ifs025")
    assert len(out) == 2
    assert "latitude=19.30000%2C19.40000" in seen["url"] and "models=ecmwf_ifs025" in seen["url"]
    assert out[0]["grid_elev_m"] == 2700.0 and out[1]["grid_elev_m"] == 2800.0
    assert out[0]["tmin_c"] == pytest.approx(1.0) and out[1]["tmin_c"] == pytest.approx(-2.0)
    with pytest.raises(RuntimeError):
        fc.parse_batch([loc(0, 1)], 2, "2026-01-10", "ecmwf_ifs025")
