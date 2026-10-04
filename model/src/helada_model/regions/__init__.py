"""Region configs for the country swap: same pipeline, different place.

    import helada_model.regions as regions
    r = regions.load("puno")          # -> Region (config dict + helpers)
    regions.available()               # -> ["puno", "toluca"]

Configs are YAML files in `model/regions/` (override with the env var HELADA_REGIONS_DIR). Each one states
the bbox, crops with frost thresholds and sources, languages, channel notes, the loss program and whether station
data exists. `model.mode` decides how the runner answers:
  helada_model   -> the trained Toluca product via helada_model.predict()
  forecast_only  -> no station data: the raw day-1 forecast with a band measured against ERA5 plus a site prior,
                    and an explicit precondition (loggers / SENAMHI). No learned correction, no skill claim.
Runner: `uv run python -m helada_model.regions.demo puno --date 2025-07-10`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import _yaml

MODES = ("helada_model", "forecast_only")
REQUIRED = ("name", "country", "timezone", "bbox", "model", "data", "crops", "program", "languages", "channel", "parcels")


def regions_dir() -> Path:
    env = os.environ.get("HELADA_REGIONS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "regions"   # src/helada_model/regions -> model/regions


def available() -> list[str]:
    d = regions_dir()
    return sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []


@dataclass
class Region:
    name: str
    config: dict
    path: Path
    _calibration: dict | None = field(default=None, repr=False)

    @property
    def mode(self) -> str:
        return self.config["model"]["mode"]

    @property
    def stations_available(self) -> bool:
        return bool(self.config["data"]["stations_available"])

    def in_bbox(self, lat: float, lon: float) -> bool:
        b = self.config["bbox"]
        return b["lat"][0] <= lat <= b["lat"][1] and b["lon"][0] <= lon <= b["lon"][1]

    def crop(self, crop_id: str | None = None) -> dict:
        crops = self.config["crops"]
        if crop_id is None:
            return crops[0]
        for c in crops:
            if c["id"] == crop_id:
                return c
        raise KeyError(f"region {self.name} has no crop {crop_id!r}; choose one of {[c['id'] for c in crops]}")

    def crop_in_field(self, crop_id: str | None, date: str) -> bool:
        """True if `date` (YYYY-MM-DD) falls inside the crop's in-field window (MM-DD pair; may wrap the year)."""
        a, b = self.crop(crop_id)["in_field"]
        md = date[5:10]
        return (a <= md <= b) if a <= b else (md >= a or md <= b)

    def parcels(self):
        from .. import Parcel
        c = self.crop()["id"]
        return [Parcel(p["id"], float(p["lat"]), float(p["lon"]), municipality=p.get("place", ""), crop=c)
                for p in self.config["parcels"]]

    def calibration(self) -> dict | None:
        """The measured band file named in model.calibration (relative to the regions dir), or None."""
        rel = self.config["model"].get("calibration")
        if not rel:
            return None
        if self._calibration is None:
            fp = self.path.parent / rel
            if not fp.exists():
                return None
            self._calibration = json.loads(fp.read_text())
        return self._calibration


def validate(cfg: dict, name: str = "?") -> None:
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        raise ValueError(f"region {name}: missing keys {missing}")
    if cfg["model"].get("mode") not in MODES:
        raise ValueError(f"region {name}: model.mode must be one of {MODES}")
    b = cfg["bbox"]
    if not (len(b["lat"]) == 2 and len(b["lon"]) == 2 and b["lat"][0] < b["lat"][1] and b["lon"][0] < b["lon"][1]):
        raise ValueError(f"region {name}: bbox must be lat/lon [min, max]")
    if not isinstance(cfg["data"].get("stations_available"), bool):
        raise ValueError(f"region {name}: data.stations_available must be true/false")
    for c in cfg["crops"]:
        for k in ("id", "crit_plant_c", "alert_air_tmin_c", "in_field", "src"):
            if k not in c:
                raise ValueError(f"region {name}: crop {c.get('id')} missing {k}")
        for s in c["src"]:
            if s not in cfg.get("sources", {}):
                raise ValueError(f"region {name}: crop {c['id']} cites unknown source {s}")
    for p in cfg["parcels"]:
        if not (b["lat"][0] <= p["lat"] <= b["lat"][1] and b["lon"][0] <= p["lon"] <= b["lon"][1]):
            raise ValueError(f"region {name}: parcel {p['id']} is outside the bbox")
    if cfg["model"]["mode"] == "helada_model" and not cfg["data"]["stations_available"]:
        raise ValueError(f"region {name}: the trained model mode needs station data")


def load(name: str) -> Region:
    """Load and validate a region config by name (file `<regions_dir>/<name>.yaml`)."""
    fp = regions_dir() / f"{name}.yaml"
    if not fp.exists():
        raise FileNotFoundError(f"no region config {fp}; available: {available()}")
    cfg = _yaml.load_text(fp.read_text(encoding="utf-8"))
    validate(cfg, name)
    return Region(name=cfg["name"], config=cfg, path=fp)


__all__ = ["Region", "load", "available", "validate", "regions_dir"]
