"""Demo roster + SMN stations (for the 'nearest station' line of the packet)."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .config import BACKEND_DIR
from .db import DB

ROSTER_FILE = BACKEND_DIR / "data" / "roster.json"

# Subset of SMN climatological stations in the Toluca / Ixtlahuaca valleys
# (station, name, lat, lon, alt).
STATIONS = [
    ("15010", "Atotonilco (Almoloya de Juárez)", 19.46222, -99.77556, 2557),
    ("15014", "Capulhuac (Otzolotepec)", 19.44083, -99.54528, 2760),
    ("15076", "Presa Tepetitlán (San Felipe del Progreso)", 19.6625, -99.95778, 2564),
    ("15085", "San Bartolo del Llano (Ixtlahuaca)", 19.52444, -99.74139, 2587),
    ("15086", "San Bernabé (Temoaya)", 19.47611, -99.71444, 2560),
    ("15126", "Toluca Obs. (Zinacantepec)", 19.29111, -99.71417, 2726),
    ("15133", "Presa Villa Victoria", 19.46111, -100.05389, 2552),
    ("15201", "Trojes (Temoaya)", 19.42806, -99.6125, 2583),
    ("15205", "Dolores Presa (Villa Victoria)", 19.39917, -99.90833, 2616),
    ("15238", "Santa María del Llano (Ixtlahuaca)", 19.51472, -99.72861, 2618),
    ("15251", "Atlacomulco II (DGE)", 19.7975, -99.87444, 2574),
    ("15266", "CONAGUA Edomex (Metepec)", 19.24833, -99.57556, 2762),
    ("15282", "Tres Barrancas (Almoloya de Juárez)", 19.34083, -99.79833, 2682),
    ("15372", "Ixtlahuaca (DGE)", 19.56889, -99.76694, 2540),
    ("15390", "E.T.A. 013 Jocotitlán", 19.71111, -99.78889, 2650),
    ("15026", "Enyeje (Ixtlahuaca)", 19.56389, -99.85, 2550),
]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_station(lat: float, lon: float) -> dict:
    best = min(STATIONS, key=lambda s: haversine_m(lat, lon, s[2], s[3]))
    return {"station": best[0], "name": best[1], "lat": best[2], "lon": best[3], "alt_m": best[4],
            "distance_km": round(haversine_m(lat, lon, best[2], best[3]) / 1000, 1)}


def load_roster_file() -> list[dict]:
    return json.loads(Path(ROSTER_FILE).read_text(encoding="utf-8"))["parcels"]


def seed(db: DB) -> None:
    for p in load_roster_file():
        db.execute(
            "INSERT OR REPLACE INTO parcels(parcel_id, phone, data) VALUES (?,?,?)",
            (p["parcel_id"], p["phone"], json.dumps(p, ensure_ascii=False)),
        )


def all_parcels(db: DB) -> list[dict]:
    return [json.loads(r["data"]) for r in db.all("SELECT data FROM parcels ORDER BY parcel_id")]


def get_parcel(db: DB, parcel_id: str) -> dict | None:
    r = db.one("SELECT data FROM parcels WHERE parcel_id=?", (parcel_id,))
    return json.loads(r["data"]) if r else None


def parcel_by_phone(db: DB, phone: str) -> dict | None:
    phone = normalize_phone(phone)
    r = db.one("SELECT data FROM parcels WHERE phone=?", (phone,))
    return json.loads(r["data"]) if r else None


def normalize_phone(phone: str) -> str:
    p = (phone or "").strip()
    if p.startswith("whatsapp:"):
        p = p[len("whatsapp:"):]
    p = p.replace(" ", "").replace("-", "")
    # Mexican mobiles: WhatsApp/Twilio may deliver +521XXXXXXXXXX (legacy mobile prefix) for the
    # same line stored as +52XXXXXXXXXX. Canonical form drops the "1" so both match the roster.
    if p.startswith("+521") and len(p) == 14:
        p = "+52" + p[4:]
    return p
