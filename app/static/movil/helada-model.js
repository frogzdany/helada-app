/* helada-model.js: the frost model, running on the phone.
 *
 * A line-for-line port of the inference path of `helada_model` (model/src/helada_model: features.py, core.py,
 * calib.py, logger.py) plus the alert rule of app/backend/alerts.py. It reads the SAME LightGBM text artifacts the
 * server uses (model_*.txt.gz, 372 KB in total) and a per-parcel "site pack" built by scripts/build_movil_pack.py
 * (terrain features need the DEM, so they are computed once when the parcel is registered).
 *
 * No network, no dependencies. tests/test_movil_parity.py checks every output against the Python model.
 * Works as a classic <script> (window.HeladaModel), in a worker, and in Node (module.exports).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HeladaModel = api;
})(typeof self !== "undefined" ? self : globalThis, function () {
  "use strict";

  const LAPSE_C_PER_M = 0.0065;
  const WEATHER = ["fc_tmin", "fc_dew", "fc_cloud", "fc_wind", "fc_dtr", "fc_dep", "clear_calm", "doy_s", "doy_c"];
  const K_ZERO = 1e-35;

  // ------------------------------------------------------------------ LightGBM text model
  /** Parse a LightGBM text dump (Booster.model_to_string) into arrays. Numerical splits only. */
  function parseLightGBM(text) {
    const head = text.slice(0, text.indexOf("Tree=0"));
    const names = (/^feature_names=(.*)$/m.exec(head) || [, ""])[1].trim().split(/\s+/);
    if (/^num_class=(\d+)$/m.exec(head) && +/^num_class=(\d+)$/m.exec(head)[1] !== 1) throw new Error("multiclass model");
    const trees = [];
    const blocks = text.split(/\nTree=\d+\n/).slice(1);
    for (const b of blocks) {
      const kv = {};
      for (const line of b.split("\n")) {
        if (!line || line.startsWith("end of trees")) break;
        const i = line.indexOf("=");
        if (i > 0) kv[line.slice(0, i)] = line.slice(i + 1);
      }
      if (kv.num_cat && +kv.num_cat !== 0) throw new Error("categorical splits are not supported");
      if (kv.is_linear && +kv.is_linear !== 0) throw new Error("linear trees are not supported");
      const nums = (s) => (s ? s.trim().split(/\s+/).map(Number) : []);
      trees.push({
        sf: Int32Array.from(nums(kv.split_feature)), th: Float64Array.from(nums(kv.threshold)),
        dt: Uint8Array.from(nums(kv.decision_type)), lc: Int32Array.from(nums(kv.left_child)),
        rc: Int32Array.from(nums(kv.right_child)), lv: Float64Array.from(nums(kv.leaf_value)),
      });
    }
    return { featureNames: names, trees };
  }

  /** Raw score of one feature vector (regression objective: the sum of the leaves). */
  function predictTrees(model, x) {
    let s = 0;
    for (const t of model.trees) {
      if (t.sf.length === 0) { s += t.lv[0]; continue; }
      let node = 0;
      while (node >= 0) {
        let v = x[t.sf[node]];
        const d = t.dt[node], missing = (d >> 2) & 3, defLeft = (d & 2) !== 0;
        let left;
        if (Number.isNaN(v) && missing !== 2) v = 0;
        if ((missing === 1 && Math.abs(v) <= K_ZERO) || (missing === 2 && Number.isNaN(v))) left = defLeft;
        else left = v <= t.th[node];
        node = left ? t.lc[node] : t.rc[node];
      }
      s += t.lv[~node];
    }
    return s;
  }

  // ------------------------------------------------------------------ dates
  const DAY = 86400000;
  const utc = (iso) => { const [y, m, d] = iso.split("-").map(Number); return Date.UTC(y, m - 1, d); };
  const iso = (ms) => new Date(ms).toISOString().slice(0, 10);
  const addDays = (isoDate, n) => iso(utc(isoDate) + n * DAY);
  const dayOfYear = (isoDate) => Math.round((utc(isoDate) - Date.UTC(+isoDate.slice(0, 4), 0, 1)) / DAY) + 1;
  const daysBetween = (a, b) => Math.round((utc(b) - utc(a)) / DAY);

  // ------------------------------------------------------------------ features.py
  function clearCalm(cloudPct, windKmh) {
    const c = Math.min(Math.max(1 - cloudPct / 100, 0), 1);
    return c * Math.exp(-windKmh / 8);
  }

  function magnusDewpoint(tC, rhPct) {
    const a = 17.62, b = 243.12;
    const g = Math.log(Math.max(Math.min(rhPct, 100), 1) / 100) + (a * tC) / (b + tC);
    return (b * g) / (a - g);
  }

  function pick(d, keys) {
    for (const k of keys) {
      const v = d[k];
      if (v !== undefined && v !== null && !(typeof v === "number" && Number.isNaN(v))) return Number(v);
    }
    return null;
  }

  /** Weather feature row from a scalar night forecast (same aliases and neutral fallbacks as features.py). */
  function nightFromScalars(d, eveningDate) {
    const tmin = pick(d, ["tmin_c", "grid_tmin_c", "fc_tmin", "temperature_2m_min"]);
    if (tmin === null) throw new Error("forecast needs a night minimum temperature (tmin_c)");
    let fallback = 0;
    let dew = pick(d, ["dew_c", "dewpoint_c", "dew_point_c", "fc_dew"]);
    if (dew === null) {
      const rh = pick(d, ["rh", "rh_pct", "relative_humidity", "relative_humidity_2m"]);
      if (rh !== null) dew = magnusDewpoint(tmin + 3.0, rh);
      else { dew = tmin - 4.0; fallback += 1; }
    }
    let cloud = pick(d, ["cloud_pct", "cloud_cover", "fc_cloud"]);
    if (cloud === null) { cloud = 40.0; fallback += 1; }
    let wind = pick(d, ["wind_kmh", "wind_speed_10m", "fc_wind"]);
    if (wind === null) {
      const wms = pick(d, ["wind_ms", "wind_speed_ms"]);
      if (wms !== null) wind = 3.6 * wms;
      else { wind = 6.0; fallback += 1; }
    }
    let tmax = pick(d, ["tmax_prev_c", "tmax_c", "fc_tmax_prev", "temperature_2m_max"]);
    if (tmax === null) { tmax = tmin + 16.0; fallback += 1; }
    const doy = dayOfYear(addDays(eveningDate, 1));   // the model keys nights by the MORNING date
    return {
      fc_tmin: tmin, fc_dew: dew, fc_cloud: cloud, fc_wind: wind, fc_tmax_prev: tmax,
      doy_s: Math.sin((2 * Math.PI * doy) / 365.25), doy_c: Math.cos((2 * Math.PI * doy) / 365.25),
      fc_dtr: tmax - tmin, fc_dep: tmin - dew, clear_calm: clearCalm(cloud, wind), fallback,
    };
  }

  /** Open-Meteo hourly block -> {eveningDate: scalar night dict}. Night of X = X 18:00 .. X+1 08:00 local. */
  function nightsFromHourly(hourly, gridElevM) {
    const col = (name) => {
      const k = Object.keys(hourly).find((c) => c === name || c.startsWith(name + "_"));
      return k ? hourly[k] : [];
    };
    const time = hourly.time || [], T = col("temperature_2m"), D = col("dew_point_2m"), C = col("cloud_cover"), W = col("wind_speed_10m");
    const nights = {}, tmax = {};
    const ok = (v) => v !== null && v !== undefined && !Number.isNaN(v);
    for (let i = 0; i < time.length; i++) {
      const date = time[i].slice(0, 10), hh = +time[i].slice(11, 13);
      if (hh >= 12 && hh <= 17 && ok(T[i])) tmax[date] = Math.max(tmax[date] === undefined ? -Infinity : tmax[date], T[i]);
      let ev = null;
      if (hh >= 18) ev = date; else if (hh <= 8) ev = addDays(date, -1);
      if (ev === null) continue;
      const n = nights[ev] || (nights[ev] = { t: [], d: [], c: [], w: [] });
      if (ok(T[i])) n.t.push(T[i]);
      if (ok(D[i])) n.d.push(D[i]);
      if (ok(C[i])) n.c.push(C[i]);
      if (ok(W[i])) n.w.push(W[i]);
    }
    const mean = (a) => (a.length ? a.reduce((s, v) => s + v, 0) / a.length : null);
    const out = {};
    for (const ev of Object.keys(nights)) {
      const n = nights[ev];
      if (n.t.length < 12) continue;                    // incomplete night window
      out[ev] = {
        tmin_c: Math.min(...n.t), dew_c: mean(n.d), cloud_pct: mean(n.c), wind_kmh: mean(n.w),
        tmax_prev_c: tmax[ev] === undefined ? null : tmax[ev],
        grid_elev_m: ok(gridElevM) ? gridElevM : null,
      };
    }
    return out;
  }

  // ------------------------------------------------------------------ calib.py
  function searchLeft(a, v) { let i = 0; while (i < a.length && a[i] < v) i++; return i; }
  function interp(x, xp, fp, left, right) {
    const n = xp.length;
    if (x <= xp[0]) return x < xp[0] && left !== undefined ? left : fp[0];
    if (x >= xp[n - 1]) return x > xp[n - 1] && right !== undefined ? right : fp[n - 1];
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xp[m] <= x) lo = m; else hi = m; }
    const dx = xp[hi] - xp[lo];
    return dx === 0 ? fp[lo] : fp[lo] + ((x - xp[lo]) * (fp[hi] - fp[lo])) / dx;
  }
  const clip = (v, a, b) => Math.min(Math.max(v, a), b);

  /** P(Tmin <= 0), and the 80% interval, from the out-of-sample residual distribution of the regime. */
  function calibApply(c, pred, cc) {
    const i = clip(searchLeft(c.cc_edges, cc) - 1, 0, 2);
    const j = clip(searchLeft(c.pred_edges, pred) - 1, 0, c.pred_edges.length - 2);
    const q = c.bins[i + "_" + j].q, qs = c.qs;
    let p = interp(-pred, q, qs, 0.0, 1.0);
    const lo = pred + interp(0.10, qs, q), hi = pred + interp(0.90, qs, q);
    if (c.iso) p = interp(p, c.iso.x, c.iso.y);
    return { p: clip(p, 0.001, 0.995), lo, hi };
  }

  // ------------------------------------------------------------------ logger.py
  function levelFor(levels, k) {
    const lv = levels.slice().sort((a, b) => a.k - b.k).filter((x) => x.k <= Math.max(k | 0, 0));
    return lv[lv.length - 1];
  }

  /** Empirical-Bayes posterior mean (a, b) of the site correction r = a + b*clear_calm. No nights -> (0, 0). */
  function posterior(cc, r, hyper) {
    if (!r.length) return { a: 0, b: 0 };
    const S = hyper.Sigma, w = 1 / ((hyper.sigma2 * (1 + hyper.rho)) / (1 - hyper.rho));
    const detS = S[0][0] * S[1][1] - S[0][1] * S[1][0];
    const Si = [[S[1][1] / detS, -S[0][1] / detS], [-S[1][0] / detS, S[0][0] / detS]];
    let n = 0, sx = 0, sxx = 0, sr = 0, sxr = 0;
    for (let i = 0; i < r.length; i++) { n += 1; sx += cc[i]; sxx += cc[i] * cc[i]; sr += r[i]; sxr += cc[i] * r[i]; }
    const M = [[w * n + Si[0][0], w * sx + Si[0][1]], [w * sx + Si[1][0], w * sxx + Si[1][1]]];
    const det = M[0][0] * M[1][1] - M[0][1] * M[1][0];
    const C = [[M[1][1] / det, -M[0][1] / det], [-M[1][0] / det, M[0][0] / det]];
    return { a: C[0][0] * w * sr + C[0][1] * w * sxr, b: C[1][0] * w * sr + C[1][1] * w * sxr };
  }

  // ------------------------------------------------------------------ core.py
  function weatherFor(fd, eveningDate, terrain) {
    const f = Object.assign({}, fd);
    const ge = f.grid_elev_m;
    let adj = 0;
    if (ge !== null && ge !== undefined && !Number.isNaN(Number(ge))) {
      adj = -LAPSE_C_PER_M * (terrain.elev - Number(ge));
      for (const k of ["tmin_c", "grid_tmin_c", "fc_tmin", "tmax_prev_c", "tmax_c", "fc_tmax_prev"]) {
        if (f[k] !== undefined && f[k] !== null) f[k] = Number(f[k]) + adj;
      }
    }
    return { w: nightFromScalars(f, eveningDate), adj };
  }

  const vec = (feats, names) => names.map((k) => feats[k]);

  function transferPrediction(pack, models, parcel, w) {
    const feats = Object.assign({}, w, parcel.terrain, { lat: parcel.lat, lon: parcel.lon });
    const f = pack.meta.features;
    return 0.5 * (predictTrees(models.transfer_wx, vec(feats, f.transfer_wx)) + predictTrees(models.transfer_terrain, vec(feats, f.transfer_terrain)));
  }

  /** Site correction from the parcel's own logger nights strictly BEFORE `eveningDate` (no leakage). */
  function loggerFit(pack, models, parcel, nights, eveningDate) {
    const use = (nights || []).filter((n) => n.date < eveningDate && n.forecast);
    if (!use.length) return null;
    const cc = [], r = [];
    for (const n of use) {
      const { w } = weatherFor(n.forecast, n.date, parcel.terrain);
      cc.push(w.clear_calm); r.push(Number(n.tmin_c) - transferPrediction(pack, models, parcel, w));
    }
    const ab = posterior(cc, r, pack.logger.hyper);
    return { a: ab.a, b: ab.b, n: use.length, level: levelFor(pack.logger.levels, use.length) };
  }

  /**
   * Parcel-level night minimum + calibrated P(frost) for the night starting on the evening of `eveningDate`.
   * models = {anchor, transfer_wx, transfer_terrain} from parseLightGBM; fd = scalar night forecast;
   * loggerNights = [{date, tmin_c, forecast}] (the parcel's own thermometer readings), optional.
   */
  function predictNight(pack, models, parcel, eveningDate, fd, loggerNights) {
    const { w, adj } = weatherFor(fd, eveningDate, parcel.terrain);
    const anchored = !!parcel.anchored;
    let pred, logger = null;
    if (anchored) {
      const st = parcel.station;
      const feats = Object.assign({}, w, st.feats);
      pred = predictTrees(models.anchor, vec(feats, pack.meta.features.anchor)) + st.a + st.b * w.clear_calm;
    } else {
      pred = transferPrediction(pack, models, parcel, w);
      logger = loggerFit(pack, models, parcel, loggerNights, eveningDate);
      if (logger) pred += logger.a + logger.b * w.clear_calm;
    }
    const cal = logger ? logger.level.calibration : pack.meta.calibration[anchored ? "known_station" : "unseen_site"];
    const c = calibApply(cal, pred, w.clear_calm);
    const lo = Math.min(c.lo - 0.5 * w.fallback, pred), hi = Math.max(c.hi + 0.5 * w.fallback, pred);
    const support = logger ? "logger-anchored (" + logger.n + " noches)" : anchored ? "station-anchored" : "terrain-transfer";
    return {
      parcel_id: parcel.parcel_id, date: eveningDate, support,
      grid_tmin_c: w.fc_tmin - adj, tmin_c: pred, tmin_lo_c: lo, tmin_hi_c: hi, p_frost: clip(c.p, 0, 1),
      drivers: {
        clear_calm: w.clear_calm, clear_sky: 1 - w.fc_cloud / 100, wind_kmh: w.fc_wind, dewpoint_depression_c: w.fc_dep,
        correction_c: pred - (w.fc_tmin - adj), lapse_adj_c: adj, forecast_fallback: w.fallback,
        logger_nights: logger ? logger.n : 0, logger_expected_mae_c: logger ? logger.level.mae_c : 0,
        nearest_station_km: parcel.station ? parcel.station.km : null,
      },
    };
  }

  // ------------------------------------------------------------------ alerts.py + the fail-safe
  /** 'alert' | 'watch' | null: the same deterministic rule the server uses. */
  function alertLevel(pack, fc) {
    const A = pack.alert;
    if (fc.p_frost >= A.p_frost_min) return "alert";
    const s = fc.support || "";
    const watchRegime = s === "terrain-transfer" || (s.startsWith("logger-anchored") && fc.drivers.logger_nights < A.logger_full_season);
    if (watchRegime && fc.tmin_lo_c <= A.tmin_lo_max) return "watch";
    return null;
  }

  /**
   * What the phone is allowed to say. The model never answers beyond its evidence: when the data behind tonight's
   * number is not enough, the state is "no_seguro" and the screen sends the farmer to a person.
   *   ctx = { leadDays } = days between the forecast download and the night it is used for (0 or 1 = fresh).
   * Returns { state: 'riesgo' | 'no_seguro' | 'sin_riesgo', reasons: [...], level }.
   */
  function judge(pack, fc, ctx) {
    const R = pack.failsafe, reasons = [];
    if (!fc) return { state: "no_seguro", reasons: ["sin_pronostico"], level: null };
    const lead = (ctx && ctx.leadDays) || 0;
    const level = alertLevel(pack, fc);
    if (lead > R.max_lead_days) reasons.push("pronostico_viejo");
    if (fc.drivers.forecast_fallback >= R.max_fallback) reasons.push("datos_incompletos");
    if (level === "watch") reasons.push("sin_estacion");
    if (lead > R.fresh_lead_days && level !== "alert" && fc.p_frost >= R.stale_gray_p) reasons.push("pronostico_atrasado");
    if (reasons.length) return { state: "no_seguro", reasons, level };
    return { state: level === "alert" ? "riesgo" : "sin_riesgo", reasons, level };
  }

  /** Advisory stage + actions for a crop and month, from the fixed table (the tool cannot say anything else). */
  function stageActions(pack, crop, month) {
    const crops = pack.advisory.crops, c = crops[crop] || crops._default;
    for (const st of c.stages) if (st.months.indexOf(month) >= 0) return st;
    return crops._default.stages[0];
  }

  /** Evening date of the current night for a local Date: before 09:00 the night that started yesterday is still "tonight". */
  function tonight(now) {
    const d = new Date(now.getTime() - (now.getHours() < 9 ? DAY : 0));
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  return {
    parseLightGBM, predictTrees, clearCalm, magnusDewpoint, nightFromScalars, nightsFromHourly, calibApply, levelFor,
    posterior, loggerFit, predictNight, alertLevel, judge, stageActions, tonight, addDays, daysBetween, dayOfYear,
    WEATHER, LAPSE_C_PER_M,
  };
});
