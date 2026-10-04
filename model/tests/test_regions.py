"""Region configs (country swap) and the demo runner. No network: Open-Meteo calls are mocked."""
import json
import urllib.request
from datetime import date as _date

import pandas as pd
import pytest

import helada_model.regions as regions
from helada_model.regions import _yaml, demo


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access in tests")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def fake_openmeteo(lats, date, tmins, prev=True, model="ecmwf_ifs025"):
    """Multi-location Open-Meteo hourly response with a night minimum of tmins[i] at 06:00 of date+1."""
    t = pd.date_range(date, periods=48, freq="h")
    hours = ((t - pd.Timestamp(date) - pd.Timedelta(hours=30)) / pd.Timedelta(hours=1)).to_numpy()
    suf = "_previous_day1" if prev else ""
    out = []
    for lat, tm in zip(lats, tmins):
        temp = [round(tm + 0.35 * abs(h), 2) for h in hours]   # V-shaped, minimum at 06:00 next morning
        out.append(dict(latitude=lat, elevation=3850.0, hourly={
            "time": [x.strftime("%Y-%m-%dT%H:%M") for x in t],
            f"temperature_2m{suf}": temp, f"dew_point_2m{suf}": [tm - 6] * 48,
            f"cloud_cover{suf}": [10.0] * 48, f"wind_speed_10m{suf}": [5.0] * 48}))
    return out


# ---------- config loading ----------

def test_available_and_load():
    assert {"puno", "toluca"} <= set(regions.available())
    p, t = regions.load("puno"), regions.load("toluca")
    assert p.mode == "forecast_only" and not p.stations_available
    assert t.mode == "helada_model" and t.stations_available
    assert p.config["country"] == "PE" and t.config["country"] == "MX"
    assert p.config["timezone"] == "America/Lima"
    ids = {c["id"] for c in p.config["crops"]}
    assert {"papa", "papa_amarga"} <= ids
    # potato thresholds are cited, and the bitter potato tolerates more cold
    assert regions.load("puno").crop("papa_amarga")["crit_plant_c"][1] < p.crop("papa")["crit_plant_c"][0]
    for c in p.config["crops"]:
        assert all(s in p.config["sources"] for s in c["src"])
    assert p.config["languages"]["asr_claim"] == "none"
    assert "SAC" in p.config["program"]["name"]
    assert len(p.parcels()) == 10 and all(p.in_bbox(x.lat, x.lon) for x in p.parcels())
    assert all(x.crop == "papa" for x in p.parcels())


def test_unknown_region_and_crop():
    with pytest.raises(FileNotFoundError):
        regions.load("atlantis")
    with pytest.raises(KeyError):
        regions.load("puno").crop("maiz_temporal")


def test_crop_in_field_wraps_year():
    p = regions.load("puno")
    assert p.crop_in_field("papa", "2026-02-10")
    assert p.crop_in_field("papa", "2025-11-01")
    assert not p.crop_in_field("papa", "2025-07-10")
    assert regions.load("toluca").crop_in_field(None, "2025-08-15")


def test_validate_rejects_bad_configs():
    cfg = regions.load("puno").config
    bad = json.loads(json.dumps(cfg)); del bad["bbox"]
    with pytest.raises(ValueError, match="missing"):
        regions.validate(bad)
    bad = json.loads(json.dumps(cfg)); bad["parcels"][0]["lat"] = 19.3
    with pytest.raises(ValueError, match="outside the bbox"):
        regions.validate(bad)
    bad = json.loads(json.dumps(cfg)); bad["model"]["mode"] = "helada_model"
    with pytest.raises(ValueError, match="station data"):
        regions.validate(bad)
    bad = json.loads(json.dumps(cfg)); bad["crops"][0]["src"] = ["S99"]
    with pytest.raises(ValueError, match="unknown source"):
        regions.validate(bad)


def test_regions_dir_env_override(tmp_path, monkeypatch):
    src = regions.regions_dir() / "puno.yaml"
    (tmp_path / "puno2.yaml").write_text(src.read_text().replace("name: puno", "name: puno2", 1))
    monkeypatch.setenv("HELADA_REGIONS_DIR", str(tmp_path))
    assert regions.available() == ["puno2"]
    r = regions.load("puno2")
    assert r.name == "puno2" and r.calibration() is None      # no out/ dir there


def test_yaml_subset_parser():
    text = """
# comment
name: x   # trailing comment
n: 3
f: -1.5
flag: true
none: null
url: "http://a.b/c?d=1#frag"
bbox:
  lat: [-16.9, -14.5]
list:
  - a
  - "b: c"
items:
  - id: p1
    lat: -16.0
  - id: p2
    tags: [x, 'y', 2]
nested:
  inner:
    - 1
"""
    v = _yaml.parse(text)
    assert v == {"name": "x", "n": 3, "f": -1.5, "flag": True, "none": None, "url": "http://a.b/c?d=1#frag",
                 "bbox": {"lat": [-16.9, -14.5]}, "list": ["a", "b: c"],
                 "items": [{"id": "p1", "lat": -16.0}, {"id": "p2", "tags": ["x", "y", 2]}],
                 "nested": {"inner": [1]}}
    with pytest.raises(ValueError):
        _yaml.parse("a: {b: 1}")


def test_yaml_subset_matches_pyyaml_on_shipped_configs():
    yaml = pytest.importorskip("yaml")
    for n in ("puno", "toluca"):
        t = (regions.regions_dir() / f"{n}.yaml").read_text()
        assert _yaml.parse(t) == yaml.safe_load(t)


# ---------- runner ----------

def test_runner_puno_offline_from_cache():
    res = demo.run("puno", "2025-07-10")
    assert "cached" in res["source"]
    assert res["mode"] == "forecast_only" and not res["in_field"]
    assert len(res["rows"]) == 10
    for r in res["rows"]:
        assert r["tmin"] == r["fc_tmin"]                 # no correction claimed
        assert r["lo"] < r["tmin"] < r["hi"] and r["hi"] - r["lo"] > 5.0   # wide, honest band
        assert r["support"] == "forecast-only"
        assert r["action"].startswith("no crop alert")   # potato not in the field in July
        assert r["ref"] is not None                      # ERA5 shown for reference
    txt = demo.format_table(res)
    assert "REANALYSIS (not observations)" in txt and "PRECONDITION" in txt and "SENAMHI" in txt


def test_runner_puno_fetch_mocked(monkeypatch):
    p = regions.load("puno")
    lats = [x["lat"] for x in p.config["parcels"]]
    tmins = [-4.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0]
    seen = {}

    def fake(url, params, timeout=60.0):
        seen.update(url=url, params=params)
        return fake_openmeteo(lats, params["start_date"], tmins, prev=True)
    monkeypatch.setattr(demo, "_http_json", fake)
    res = demo.run("puno", "2023-02-10")                  # not in the cache -> archived day-1 run
    assert seen["url"] == demo.PREV_URL
    assert seen["params"]["timezone"] == "America/Lima" and seen["params"]["models"] == "ecmwf_ifs025"
    assert "temperature_2m_previous_day1" in seen["params"]["hourly"]
    assert res["in_field"]
    got = [r["fc_tmin"] for r in res["rows"]]
    assert got == pytest.approx(tmins, abs=0.01)
    ps = [r["p"] for r in res["rows"]]
    assert all(a >= b for a, b in zip(ps, ps[1:]))       # colder forecast -> higher P
    assert res["rows"][0]["action"] == "ALERT" and res["rows"][-1]["action"] == "-"
    assert all(r["ref"] is None for r in res["rows"])


def test_runner_live_endpoint_for_today(monkeypatch):
    p = regions.load("puno")
    lats = [x["lat"] for x in p.config["parcels"]]
    seen = {}

    def fake(url, params, timeout=60.0):
        seen.update(url=url, params=params)
        return fake_openmeteo(lats, params["start_date"], [1.0] * 10, prev=False)
    monkeypatch.setattr(demo, "_http_json", fake)
    d = _date.today().isoformat()
    fc, src = demo.fetch_forecasts(p, d)
    assert seen["url"] == demo.LIVE_URL and "previous_day1" not in seen["params"]["hourly"]
    assert set(fc) == {x["id"] for x in p.config["parcels"]} and "live" in src


def test_runner_toluca_uses_trained_model(monkeypatch):
    t = regions.load("toluca")
    lats = [x["lat"] for x in t.config["parcels"]]
    monkeypatch.setattr(demo, "_http_json",
                        lambda url, params, timeout=60.0: fake_openmeteo(lats, params["start_date"], [3.0, 3.0, 3.0]))
    res = demo.run("toluca", "2023-12-10")
    assert res["mode"] == "helada_model"
    sup = [r["support"] for r in res["rows"]]
    assert sup[:2] == ["station-anchored", "station-anchored"] and sup[2] == "terrain-transfer"
    assert any(r["tmin"] != r["fc_tmin"] for r in res["rows"])   # the trained model does correct here
    assert "held-out SMN" in res["band"]


def test_cli_prints_table(capsys):
    assert demo.main(["puno", "--date", "2025-01-13"]) == 0
    out = capsys.readouterr().out
    assert "PU10" in out and "forecast-only" in out and "ALERT" in out
    assert demo.main(["puno", "--date", "2025-01-13", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["region"] == "puno"
