"""Build the demo roster (backend/data/roster.json).

Coordinates are REAL points in frost-exposed Toluca/Ixtlahuaca/Atlacomulco-valley
municipalities, most ~0.9 km from an SMN station (see POINTS). Owner names, phones and areas are FICTIONAL. No parcel here
belongs to, or describes, any real person.

For each point we query the Open-Meteo elevation API (Copernicus 90 m DEM) for
the point and an 8-point ring at ~600 m to get a crude topographic position
index (TPI = elev - mean(ring); negative = hollow, cold-air pooling), and the
Open-Meteo forecast API with elevation=nan to get the raw grid-cell elevation.

Run once (needs internet):  uv run python scripts/build_roster.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "backend" / "data" / "roster.json"

# (id, fictional owner, municipality, lat, lon, area_ha, crop, lang, near_station)
# Ten parcels sit ~0.9 km from a real SMN station (DEM elevation within 50 m of the station's), so
# helada_model treats them as "station-anchored" (the validated regime). P05 and P10 are >6 km from any
# station on purpose: "terrain-transfer" (wide band, logger precondition). Coordinates are real points;
# land use at each point was NOT checked.
POINTS = [
    ("P01", "Aurelio Mendoza (ficticio)", "Almoloya de Juárez", 19.3351, -99.7923, 2.5, "maiz_temporal", "es", "15282"),
    ("P02", "Teresa Gómez (ficticia)", "Almoloya de Juárez", 19.4703, -99.7756, 1.5, "maiz_temporal", "es", "15010"),
    ("P03", "Rufino Álvarez (ficticio)", "Temoaya", 19.4818, -99.7084, 3.0, "maiz_temporal", "es", "15086"),
    ("P04", "Juana Martínez (ficticia)", "Temoaya", 19.4338, -99.6186, 1.0, "maiz_temporal", "es", "15201"),
    ("P05", "Crescencio Reyes (ficticio)", "Temoaya", 19.4950, -99.5750, 2.0, "papa", "es", None),
    ("P06", "Margarita Salinas (ficticia)", "Zinacantepec", 19.2911, -99.7227, 1.2, "maiz_temporal", "es", "15126"),
    ("P07", "Isidro Contreras (ficticio)", "Ixtlahuaca", 19.5746, -99.7609, 4.5, "maiz_temporal", "es", "15372"),
    ("P08", "Felipa Hernández (ficticia)", "Ixtlahuaca", 19.5325, -99.7414, 2.0, "maiz_temporal", "maz", "15085"),
    ("P09", "Anastasio Cruz (ficticio)", "Ixtlahuaca", 19.5090, -99.7347, 0.5, "avena", "es", "15238"),
    ("P10", "Petra Segundo (ficticia)", "San Felipe del Progreso", 19.7300, -99.9800, 2.5, "maiz_temporal", "maz", None),
    ("P11", "Macario Nava (ficticio)", "Atlacomulco", 19.8032, -99.8684, 1.8, "maiz_temporal", "maz", "15251"),
    ("P12", "Rosalía Velázquez (ficticia)", "Ixtlahuaca", 19.5582, -99.8439, 0.8, "haba", "es", "15026"),
]

RING_M = 600.0


def ring(lat: float, lon: float) -> list[tuple[float, float]]:
    pts = []
    for k in range(8):
        a = 2 * math.pi * k / 8
        dlat = RING_M * math.cos(a) / 111_320
        dlon = RING_M * math.sin(a) / (111_320 * math.cos(math.radians(lat)))
        pts.append((lat + dlat, lon + dlon))
    return pts


def main() -> None:
    out = []
    for p in POINTS:
        allp = [(p[3], p[4])] + ring(p[3], p[4])
        r = httpx.get(
            "https://api.open-meteo.com/v1/elevation",
            params={"latitude": ",".join(f"{x[0]:.5f}" for x in allp),
                    "longitude": ",".join(f"{x[1]:.5f}" for x in allp)},
            timeout=30,
        )
        r.raise_for_status()
        e = r.json()["elevation"]
        tpi = e[0] - sum(e[1:]) / 8
        g = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": p[3], "longitude": p[4], "elevation": "nan",
                    "daily": "temperature_2m_min", "forecast_days": 1},
            timeout=30,
        ).json()
        out.append({
            "parcel_id": p[0], "owner_name": p[1], "municipality": p[2],
            # how the farmer is addressed: a field of the roster, never guessed from the name
            "trato": "doña" if p[1].endswith("(ficticia)") else "don",
            "lat": p[3], "lon": p[4], "area_ha": p[5], "crop": p[6], "lang": p[7],
            "phone": f"+52722000{int(p[0][1:]):04d}",  # fictional, non-routable demo numbers
            "elev_m": round(e[0], 1),
            "grid_elev_m": round(g.get("elevation", e[0]), 1),
            "tpi_m": round(tpi, 1),
            "is_individual": True,
            "uga_restricted": False,
            "near_station": p[8],
            "fictional": True,
        })
        print(out[-1])
    OUT.write_text(json.dumps({
        "_note": "DEMO ROSTER. Coordinates are real points in the Toluca valley; owner names, "
                 "phones and areas are FICTIONAL. elev_m/tpi_m from Open-Meteo elevation API "
                 "(Copernicus 90 m DEM); grid_elev_m from Open-Meteo forecast API (elevation=nan). "
                 "uga_restricted is a placeholder, not checked against the POETEM layer [V]. "
                 "near_station = SMN station ~0.9 km away (station-anchored parcels); null = terrain-transfer. "
                 "Land use at each point was not checked.",
        "parcels": out,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
