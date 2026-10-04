"""Point terrain features from a small regional DEM (Copernicus GLO-30, block-averaged).

The same code computes features for training (at SMN stations) and at inference (at parcels),
so train/serve parity is exact. Everything is numpy; no GIS stack is needed at runtime.

Features (all in metres / degrees):
  elev            DEM elevation at the point (bilinear)
  tpi_300/1k/3k   elevation minus mean elevation within 300 m / 1 km / 3 km  (negative = hollow)
  dz_10k          elevation minus mean within 10 km (~ NWP grid-cell scale; "station - grid cell mean")
  havf_5k         height above valley floor: elevation minus 5th percentile within 5 km
  relief_5k       95th minus 5th percentile within 5 km
  rank_1k/3k      share of cells within 1/3 km that are LOWER than the point (0 = pit, 1 = summit)
  slope_deg       slope over ~3 cells
  northness       cos(aspect) * sin(slope)
  horizon_deg     mean horizon elevation angle over 16 azimuths up to 3 km (sky-view proxy; higher = more enclosed)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FEATURES = ["elev", "tpi_300", "tpi_1k", "tpi_3k", "dz_10k", "havf_5k", "relief_5k",
            "rank_1k", "rank_3k", "slope_deg", "northness", "horizon_deg"]

M_PER_DEG = 111_320.0


@dataclass
class DEM:
    z: np.ndarray        # (rows, cols) float32, row 0 = north edge
    lat0: float          # latitude of the north edge of row 0
    lon0: float          # longitude of the west edge of col 0
    res: float           # cell size in degrees

    def inside(self, lat: float, lon: float, margin_deg: float = 0.0) -> bool:
        r, c = self.rc(lat, lon)
        m = margin_deg / self.res
        return m <= r < self.z.shape[0] - m and m <= c < self.z.shape[1] - m

    def rc(self, lat: float, lon: float) -> tuple[float, float]:
        return (self.lat0 - lat) / self.res - 0.5, (lon - self.lon0) / self.res - 0.5

    def sample(self, lat: float, lon: float) -> float:
        r, c = self.rc(lat, lon)
        r0, c0 = int(math.floor(r)), int(math.floor(c))
        r0 = min(max(r0, 0), self.z.shape[0] - 2); c0 = min(max(c0, 0), self.z.shape[1] - 2)
        fr, fc = min(max(r - r0, 0.0), 1.0), min(max(c - c0, 0.0), 1.0)
        w = self.z[r0:r0 + 2, c0:c0 + 2].astype(np.float64)
        return float((w[0, 0] * (1 - fc) + w[0, 1] * fc) * (1 - fr) + (w[1, 0] * (1 - fc) + w[1, 1] * fc) * fr)

    def window(self, lat: float, lon: float, radius_m: float):
        """Return (z window, dy grid m, dx grid m) centred on the point."""
        r, c = self.rc(lat, lon)
        dy_cell = self.res * M_PER_DEG
        dx_cell = self.res * M_PER_DEG * math.cos(math.radians(lat))
        nr, nc = int(math.ceil(radius_m / dy_cell)) + 1, int(math.ceil(radius_m / dx_cell)) + 1
        ri, ci = int(round(r)), int(round(c))
        r_lo, r_hi = max(ri - nr, 0), min(ri + nr + 1, self.z.shape[0])
        c_lo, c_hi = max(ci - nc, 0), min(ci + nc + 1, self.z.shape[1])
        w = self.z[r_lo:r_hi, c_lo:c_hi].astype(np.float64)
        yy = (np.arange(r_lo, r_hi) - r)[:, None] * dy_cell
        xx = (np.arange(c_lo, c_hi) - c)[None, :] * dx_cell
        return w, yy, xx


def load_dem(path) -> DEM:
    d = np.load(path)
    z = d["z"].astype(np.float32)
    return DEM(z=z, lat0=float(d["lat0"]), lon0=float(d["lon0"]), res=float(d["res"]))


def point_features(dem: DEM, lat: float, lon: float) -> dict[str, float]:
    e = dem.sample(lat, lon)
    w, yy, xx = dem.window(lat, lon, 10_000)
    dist = np.sqrt(yy ** 2 + xx ** 2)
    out = {"elev": e}

    def disk(r):
        return w[dist <= r]

    for name, r in (("tpi_300", 300), ("tpi_1k", 1000), ("tpi_3k", 3000)):
        v = disk(r); out[name] = e - float(v.mean()) if v.size else 0.0
    out["dz_10k"] = e - float(disk(10_000).mean())
    v5 = disk(5000); p5, p95 = np.percentile(v5, [5, 95])
    out["havf_5k"] = e - float(p5); out["relief_5k"] = float(p95 - p5)
    out["rank_1k"] = float((disk(1000) < e).mean()); out["rank_3k"] = float((disk(3000) < e).mean())

    # slope / aspect from a 3-cell-spaced central difference
    dy = dem.res * M_PER_DEG; dx = dy * math.cos(math.radians(lat)); k = 1.5 * dem.res
    zn, zs = dem.sample(lat + k, lon), dem.sample(lat - k, lon)
    ze, zw = dem.sample(lat, lon + k), dem.sample(lat, lon - k)
    gy = (zn - zs) / (3 * dy); gx = (ze - zw) / (3 * dx)
    slope = math.atan(math.hypot(gx, gy))
    out["slope_deg"] = math.degrees(slope)
    # aspect = direction the slope faces (downhill). northness = cos(aspect)*sin(slope)
    # downhill vector = (-gx, -gy) in (east, north); cos(aspect) = north component / |g|
    g = math.hypot(gx, gy)
    out["northness"] = (-gy / g) * math.sin(slope) if g > 1e-9 else 0.0

    # horizon angle (sky-view proxy): 16 azimuths, samples every ~1 cell out to 3 km
    angs = []
    steps = np.arange(1, int(3000 / (dy * 0.75)) + 1) * dy * 0.75
    for az in np.linspace(0, 2 * np.pi, 16, endpoint=False):
        la = lat + np.cos(az) * steps / M_PER_DEG
        lo = lon + np.sin(az) * steps / (M_PER_DEG * math.cos(math.radians(lat)))
        r, c = (dem.lat0 - la) / dem.res - 0.5, (lo - dem.lon0) / dem.res - 0.5
        ri = np.clip(np.round(r).astype(int), 0, dem.z.shape[0] - 1)
        ci = np.clip(np.round(c).astype(int), 0, dem.z.shape[1] - 1)
        zz = dem.z[ri, ci].astype(np.float64)
        angs.append(max(0.0, float(np.degrees(np.arctan((zz - e) / steps)).max())))
    out["horizon_deg"] = float(np.mean(angs))
    return out
