"""Archived day-1 forecasts (Open-Meteo Previous Runs API) at each SMN station's exact coordinates.

Source: https://previous-runs-api.open-meteo.com/v1/forecast  (variables *_previous_day1 = the run ~24 h earlier)
Models: best_match (Open-Meteo default) and ecmwf_ifs025. Dew point/cloud/wind archived from Feb 2024.
Per-station files are cached in ../data/forecasts/{model}/{station}.csv.gz so the script resumes.
"""
import os, sys, time
import pandas as pd, requests

HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "..", "..", "data")
VARS = ["temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m"]
URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
st = pd.read_csv(f"{D}/smn/stations.csv", dtype={"station": str}, parse_dates=["last"])
st = st[st.n_2024 >= 150]
models = sys.argv[1:] or ["best_match", "ecmwf_ifs025"]
for model in models:
    od = f"{D}/forecasts/{model}"; os.makedirs(od, exist_ok=True)
    for _, s in st.iterrows():
        fp = f"{od}/{s.station}.csv.gz"
        if os.path.exists(fp): continue
        start = "2024-02-01" if model == "best_match" else "2024-03-01"
        end = min(s["last"] + pd.Timedelta(days=1), pd.Timestamp("2026-09-27")).strftime("%Y-%m-%d")
        p = dict(latitude=s.lat, longitude=s.lon, start_date=start, end_date=end, timezone="America/Mexico_City",
                 models=model, hourly=",".join(f"{v}_previous_day1" for v in VARS))
        for a in range(8):
            try:
                r = requests.get(URL, params=p, timeout=180)
                if r.ok: break
                print(s.station, r.status_code, r.text[:200], flush=True)
            except Exception as e:
                print(s.station, e, flush=True)
            time.sleep(min(30 * (a + 1), 300))
        else:
            continue
        j = r.json(); df = pd.DataFrame(j["hourly"])
        df.columns = [c.replace("_previous_day1", "").replace(f"_{model}", "") for c in df.columns]
        df["grid_elev"] = j["elevation"]
        df.to_csv(fp, index=False); print(model, s.station, len(df), flush=True); time.sleep(1.5)
