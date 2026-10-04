"""Photo intake checks: EXIF capture time and GPS vs. the parcel and the event date.

Honest limit: WhatsApp strips EXIF from photos sent as images (it keeps it only
when the photo is sent as a document). So "sin EXIF" is the common case on the
real channel; the packet then falls back to the server receipt time and to a
shared WhatsApp location, and says so. These checks raise the cost of fraud;
they do not prove anything (the on-site inspection does).
"""
from __future__ import annotations

from datetime import date as Date, datetime
from pathlib import Path

from PIL import ExifTags, Image

from .roster import haversine_m

EXIF_IFD = 0x8769
GPS_IFD = 0x8825
DATETIME_ORIGINAL = 36867
DATETIME = 306

NEAR_M = 500
REVIEW_M = 2000
MAX_DAYS_AFTER = 10   # photo should be taken within the 10-day notice window


def _rat(x) -> float:
    try:
        return float(x)
    except TypeError:
        return x[0] / x[1]


def _dms(v, ref) -> float:
    d, m, s = (_rat(a) for a in v)
    val = d + m / 60 + s / 3600
    return -val if ref in ("S", "W", b"S", b"W") else val


def read_exif(path: Path) -> dict:
    out: dict = {"taken_at": None, "lat": None, "lon": None, "make": None, "model": None}
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            if not ex:
                return out
            sub = ex.get_ifd(EXIF_IFD)
            dt = sub.get(DATETIME_ORIGINAL) or ex.get(DATETIME)
            if dt:
                try:
                    out["taken_at"] = datetime.strptime(str(dt).strip("\x00 "), "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
            gps = ex.get_ifd(GPS_IFD)
            if gps and 2 in gps and 4 in gps:
                out["lat"] = round(_dms(gps[2], gps.get(1, "N")), 6)
                out["lon"] = round(_dms(gps[4], gps.get(3, "E")), 6)
            out["make"] = ex.get(271)
            out["model"] = ex.get(272)
    except Exception:
        pass
    return out


def check(path: Path, parcel: dict, event_date: str | None, received_at: datetime,
          shared_location: dict | None = None) -> dict:
    """Returns a verdict dict with per-check status: ok | revisar | sin_dato."""
    ex = read_exif(path)
    v: dict = {"exif": ex, "received_at": received_at.isoformat(timespec="seconds")}
    # --- time
    t_src = "exif" if ex["taken_at"] else "recepcion"
    t = datetime.fromisoformat(ex["taken_at"]) if ex["taken_at"] else received_at
    if event_date:
        ed = Date.fromisoformat(event_date)
        days = (t.date() - ed).days
        if 0 <= days <= MAX_DAYS_AFTER:
            ts, tmsg = "ok", f"{'Tomada' if t_src == 'exif' else 'Recibida'} {days} día(s) después del evento"
        elif days < 0:
            ts, tmsg = "revisar", f"La foto es de {-days} día(s) ANTES del evento declarado"
        else:
            ts, tmsg = "revisar", f"La foto es de {days} días después del evento (más de {MAX_DAYS_AFTER})"
    else:
        ts, tmsg = "sin_dato", "Sin fecha del evento para comparar"
    if t_src == "recepcion":
        tmsg += " (sin EXIF: se usa la hora de recepción)"
    v["time"] = {"status": ts, "source": t_src, "at": t.isoformat(timespec="minutes"), "msg": tmsg}
    # --- place
    lat, lon, g_src = ex["lat"], ex["lon"], "exif"
    if lat is None and shared_location:
        lat, lon, g_src = shared_location.get("lat"), shared_location.get("lon"), "ubicacion_compartida"
    if lat is None:
        v["gps"] = {"status": "sin_dato", "source": None, "msg": "Sin GPS en la foto ni ubicación compartida"}
    else:
        dist = haversine_m(lat, lon, parcel["lat"], parcel["lon"])
        st = "ok" if dist <= NEAR_M else "revisar"
        where = "foto" if g_src == "exif" else "ubicación compartida"
        v["gps"] = {"status": st, "source": g_src, "lat": lat, "lon": lon, "distance_m": round(dist),
                    "msg": f"GPS ({where}) a {round(dist)} m de la parcela"
                           + ("" if st == "ok" else (" — revisar" if dist <= REVIEW_M else " — lejos de la parcela"))}
    v["status"] = "ok" if v["time"]["status"] == "ok" and v["gps"]["status"] == "ok" else "revisar"
    return v


def make_exif_jpeg(path: Path, *, when: datetime, lat: float | None, lon: float | None,
                   img: Image.Image | None = None) -> Path:
    """Writes a JPEG with DateTimeOriginal (+ GPS). Used for demo sample photos and tests."""
    im = img or Image.new("RGB", (640, 480), (150, 120, 60))
    ex = Image.Exif()
    ex[271] = "Helada demo"
    ex[272] = "FOTO DE EJEMPLO"
    ex[DATETIME] = when.strftime("%Y:%m:%d %H:%M:%S")
    ex.get_ifd(EXIF_IFD)[DATETIME_ORIGINAL] = when.strftime("%Y:%m:%d %H:%M:%S")
    if lat is not None and lon is not None:
        def dms(x):
            x = abs(x)
            d = int(x)
            m = int((x - d) * 60)
            s = round(((x - d) * 60 - m) * 60, 2)
            return (float(d), float(m), float(s))
        g = ex.get_ifd(GPS_IFD)
        g[1] = "N" if lat >= 0 else "S"
        g[2] = dms(lat)
        g[3] = "E" if lon >= 0 else "W"
        g[4] = dms(lon)
    im.save(path, "JPEG", exif=ex, quality=85)
    return path


_ = ExifTags  # keep import for readers looking up tag ids
