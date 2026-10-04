// Replays tests/fixtures/movil_parity.json (inputs + outputs of the Python model) through the phone's model code.
//   node tests/movil_parity.mjs        exit 0 = every case within tolerance
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const movil = join(here, "..", "static", "movil");
const HM = createRequire(import.meta.url)(join(movil, "helada-model.js"));
const fx = JSON.parse(readFileSync(join(here, "fixtures", "movil_parity.json"), "utf8"));

const packs = {}, models = {};
for (const [key, file] of [["shipped", "pack.json"], ["demo", "pack-demo.json"]]) {
  const pack = JSON.parse(readFileSync(join(movil, "data", file), "utf8"));
  packs[key] = pack; models[key] = {};
  for (const [k, fn] of Object.entries(pack.models.files)) {
    models[key][k] = HM.parseLightGBM(gunzipSync(readFileSync(join(movil, pack.models.dir, fn))).toString("utf8"));
  }
}

const TOL_C = 0.0075, TOL_P = 0.0011;   // Python rounds to 2 decimals (degC) and 3 decimals (probability)
let bad = 0, worstC = 0, worstP = 0;
const fail = (msg) => { bad += 1; if (bad <= 12) console.error("FAIL " + msg); };

for (const c of fx.cases) {
  const pack = packs[c.pack], parcel = c.demo_logger ? pack.demo.logger.site : pack.parcels.find((p) => p.parcel_id === c.parcel_id);
  const f = HM.predictNight(pack, models[c.pack], parcel, c.date, c.forecast, c.logger || []);
  const id = `${c.pack} ${c.parcel_id} ${c.date} ${JSON.stringify(c.forecast)}`;
  for (const k of ["tmin_c", "tmin_lo_c", "tmin_hi_c", "grid_tmin_c"]) {
    const d = Math.abs(f[k] - c.expect[k]); worstC = Math.max(worstC, d);
    if (!(d <= TOL_C)) fail(`${id}: ${k} js=${f[k].toFixed(4)} py=${c.expect[k]}`);
  }
  const dp = Math.abs(f.p_frost - c.expect.p_frost); worstP = Math.max(worstP, dp);
  if (!(dp <= TOL_P)) fail(`${id}: p_frost js=${f.p_frost.toFixed(4)} py=${c.expect.p_frost}`);
  if (f.support !== c.expect.support) fail(`${id}: support js=${f.support} py=${c.expect.support}`);
  if (f.drivers.forecast_fallback !== c.expect.fallback) fail(`${id}: fallback js=${f.drivers.forecast_fallback} py=${c.expect.fallback}`);
}

const nights = HM.nightsFromHourly(fx.hourly.hourly, 2600);
for (const [ev, e] of Object.entries(fx.hourly.expect)) {
  const n = nights[ev];
  if (!n) { fail(`hourly: night ${ev} missing`); continue; }
  for (const k of Object.keys(e)) {
    if (e[k] === null ? n[k] !== null : !(Math.abs(n[k] - e[k]) <= 1e-6)) fail(`hourly ${ev}: ${k} js=${n[k]} py=${e[k]}`);
  }
}
if (Object.keys(nights).length !== Object.keys(fx.hourly.expect).length) fail(`hourly: js has ${Object.keys(nights).length} nights, py ${Object.keys(fx.hourly.expect).length}`);

// the fail-safe: every state is reachable and "no_seguro" wins whenever the evidence is thin
const pk = packs.shipped, mk = models.shipped;
const anch = pk.parcels.find((p) => p.anchored), free = pk.parcels.find((p) => !p.anchored);
const cold = { tmin_c: 0.5, dew_c: -6, cloud_pct: 0, wind_kmh: 2, tmax_prev_c: 21, grid_elev_m: 2650 };
const warm = { tmin_c: 11, dew_c: 9, cloud_pct: 100, wind_kmh: 15, tmax_prev_c: 18, grid_elev_m: 2650 };
const st = (parcel, fd, lead) => HM.judge(pk, fd && HM.predictNight(pk, mk, parcel, "2026-01-15", fd, []), { leadDays: lead }).state;
const expect = (name, got, want) => { if (got !== want) fail(`failsafe ${name}: got ${got}, want ${want}`); };
expect("no forecast", st(anch, null, 0), "no_seguro");
expect("cold clear night at a station", st(anch, cold, 0), "riesgo");
expect("warm cloudy night at a station", st(anch, warm, 0), "sin_riesgo");
expect("forecast 5 days old", st(anch, warm, 5), "no_seguro");
expect("only the minimum is known", st(anch, { tmin_c: 9 }, 0), "no_seguro");
// scan for a night in each grey zone, so the two remaining rules are exercised whatever the model's numbers are
const scan = (parcels, test) => {
  for (const parcel of parcels) for (const cloud of [15, 50, 85]) for (const wind of [3, 9]) for (let t = -1; t <= 12; t += 0.25) {
    const f = HM.predictNight(pk, mk, parcel, "2026-01-15", { tmin_c: t, dew_c: t - 6, cloud_pct: cloud, wind_kmh: wind, tmax_prev_c: t + 17, grid_elev_m: 2650 }, []);
    if (test(f)) return f;
  }
  return null;
};
const watch = scan(pk.parcels.filter((p) => !p.anchored), (f) => HM.alertLevel(pk, f) === "watch");
if (!watch) fail("failsafe: no forecast puts the no-station parcel in the watch zone");
else expect("no station nearby, range reaches 0", HM.judge(pk, watch, { leadDays: 0 }).state, "no_seguro");
const grey = scan([anch], (f) => f.p_frost >= pk.failsafe.stale_gray_p && f.p_frost < pk.alert.p_frost_min);
if (!grey) fail("failsafe: no forecast puts the station parcel between the grey and the alert thresholds");
else {
  expect("cold-ish night, forecast from today", HM.judge(pk, grey, { leadDays: 0 }).state, "sin_riesgo");
  expect("cold-ish night, forecast 2 days old", HM.judge(pk, grey, { leadDays: 2 }).state, "no_seguro");
}

// screen texts: the English reading aid has the same keys and the same {placeholders} as the Spanish
const { es, en } = createRequire(import.meta.url)(join(movil, "i18n.js"));
const ph = (v) => (String(v).match(/\{[a-z0-9_]+\}/g) || []).sort().join();
for (const k of Object.keys(es)) {
  if (!(k in en)) fail(`i18n: en is missing "${k}"`);
  else if (ph(es[k]) !== ph(en[k])) fail(`i18n: placeholders differ in "${k}": es ${ph(es[k])} / en ${ph(en[k])}`);
}
for (const k of Object.keys(en)) if (!(k in es)) fail(`i18n: en has an extra key "${k}"`);
for (const lang of [es, en]) if (lang.days.split(",").length !== 7 || lang.months.split(",").length !== 12) fail("i18n: days/months lists");

console.log(`${fx.cases.length} model cases, ${Object.keys(fx.hourly.expect).length} aggregated nights; ` +
            `worst diff ${worstC.toFixed(4)} degC, ${worstP.toFixed(4)} probability; ${bad} failures`);
process.exit(bad ? 1 : 0);
