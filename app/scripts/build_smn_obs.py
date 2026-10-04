"""Build backend/data/smn_obs_recent.json.gz: observed daily Tmin at the SMN stations the app cites in packets.

Source: the shared cache ../data/smn/obs_daily.csv.gz (CONAGUA/SMN daily climatology files
`https://smn.conagua.gob.mx/tools/RESOURCES/Normales_Climatologicas/Diarios/mex/dia{ID}.txt`, downloaded and
QC'd by model/research/01_fetch_smn.py). That file is ~9 MB and gitignored, so the app ships this small extract
(stations in backend/roster.py STATIONS, from START on) and the packet reads it offline.

SMN convention: the manual 08:00 reading dated D+1 closes the night that starts on the evening of D.
The SMN publishes these files with a lag that differs by station (days to months); `last_date` per station and
`retrieved` (when the cache was downloaded) let the packet say honestly when an observation is not out yet.

Run after refreshing ../data/smn (model/research/01_fetch_smn.py):
    uv run python scripts/build_smn_obs.py
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from backend.roster import STATIONS  # noqa: E402

SRC = APP.parent / "data" / "smn" / "obs_daily.csv.gz"
OUT = APP / "backend" / "data" / "smn_obs_recent.json.gz"
START = date(2023, 7, 1)


def main() -> None:
    ids = {s[0] for s in STATIONS}
    rows: dict[str, dict[date, float]] = {s: {} for s in ids}
    with gzip.open(SRC, "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            sid = r["station"]
            if sid not in ids or r["date"] < START.isoformat() or not r["tmin"]:
                continue
            rows[sid][date.fromisoformat(r["date"])] = float(r["tmin"])
    retrieved = datetime.fromtimestamp(SRC.stat().st_mtime).date()
    stations = {}
    for s in sorted(ids):
        d = rows[s]
        if not d:
            stations[s] = {"start": None, "last_date": None, "tmin": []}
            continue
        first, last = min(d), max(d)
        n = (last - first).days + 1
        stations[s] = {"start": first.isoformat(), "last_date": last.isoformat(),
                       "tmin": [d.get(first + timedelta(days=i)) for i in range(n)]}
    out = {"source": "CONAGUA/SMN, Normales Climatológicas, datos diarios (dia{ID}.txt); QC de "
                     "model/research/01_fetch_smn.py; lectura manual de las 08:00",
           "url_template": "https://smn.conagua.gob.mx/tools/RESOURCES/Normales_Climatologicas/Diarios/mex/dia{ID}.txt",
           "retrieved": retrieved.isoformat(), "start": START.isoformat(), "stations": stations}
    with gzip.open(OUT, "wt", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB), retrieved {retrieved}")
    for s, v in stations.items():
        print(s, v["last_date"], sum(x is not None for x in v["tmin"]))


if __name__ == "__main__":
    main()
