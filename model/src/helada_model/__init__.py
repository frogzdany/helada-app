"""helada_model: parcel-level frost (night Tmin) model for rainfed maize in the Toluca valley.

Public interface (see docs/interfaces.md):
    predict(parcels, date, forecast=None) -> list[FrostForecast]
    climatology(parcel) -> FrostClimatology
    backtest_report() -> dict

`date` is the local EVENING date of the night (the night of X runs X 18:00 -> X+1 08:00; SMN's 08:00 reading
dated X+1 is the verifying observation). Extension: support_of(forecast) -> "station-anchored" | "terrain-transfer"
| "logger-anchored (k noches)"; calibrate_site(parcel, [(date, tmin_c), ...]) -> SiteCalibration, passed to
predict(..., sites={parcel_id: SiteCalibration}).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Parcel:
    parcel_id: str
    lat: float
    lon: float
    elev_m: float | None = None          # filled from DEM if None
    owner_name: str = ""
    phone: str = ""
    municipality: str = ""
    area_ha: float = 0.0
    crop: str = "maiz_temporal"
    lang: str = "es"                      # es | maz (stored; Mazahua is not supported yet)


@dataclass
class FrostForecast:
    parcel_id: str
    date: str                             # local night (evening) date YYYY-MM-DD
    grid_tmin_c: float                    # raw forecast at the grid cell (Open-Meteo)
    tmin_c: float
    tmin_lo_c: float
    tmin_hi_c: float                      # parcel model, 80% interval
    p_frost: float                        # calibrated P(Tmin <= 0 °C)
    drivers: dict[str, float]


@dataclass
class FrostClimatology:
    parcel_id: str
    last_spring_frost_doy_p50: int
    last_spring_frost_doy_p90: int
    first_autumn_frost_doy_p10: int
    first_autumn_frost_doy_p50: int
    frost_free_days_p50: int


from .core import backtest_report, climatology, predict, support_of  # noqa: E402
from .logger import SiteCalibration, calibrate_site, expected_precision  # noqa: E402

__all__ = ["Parcel", "FrostForecast", "FrostClimatology", "predict", "climatology", "backtest_report", "support_of",
           "SiteCalibration", "calibrate_site", "expected_precision"]
