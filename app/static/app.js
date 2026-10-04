"use strict";
/* Helada dashboard + phone simulator. No build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Language of the officer's screen. English by default (the reviewers read English); the header button or ?lang=es
   switches to Spanish. Every text built here is written twice, TX(spanish, english). What the farmer receives
   (alerts, voice notes, the SMS, the PDF, the chat in the phone) is Spanish whatever the screen language. */
let LANG = (() => {
  const q = new URLSearchParams(location.search).get("lang");
  if (q === "es" || q === "en") return q;
  try { const v = localStorage.getItem("helada-lang"); if (v === "es" || v === "en") return v; } catch (e) { /* ignore */ }
  return "en";
})();
const TX = (es, en) => (LANG === "es" ? es : en);
const LOCALE = () => (LANG === "es" ? "es-MX" : "en-GB");
/** Static texts of index.html: English in the markup, Spanish in data-es (and data-es-<attribute>). */
function applyStatic() {
  document.documentElement.lang = LANG;
  document.title = TX("Helada, avisos de helada por parcela", "Helada: frost alerts for each plot");
  $$("[data-es]").forEach((n) => { if (n.dataset.en == null) n.dataset.en = n.innerHTML; n.innerHTML = LANG === "es" ? n.dataset.es : n.dataset.en; });
  ["title", "placeholder", "aria-label"].forEach((a) => $$(`[data-es-${a}]`).forEach((n) => {
    if (!n.hasAttribute(`data-en-${a}`)) n.setAttribute(`data-en-${a}`, n.getAttribute(a) || "");
    n.setAttribute(a, n.getAttribute(`data-${LANG}-${a}`));
  }));
}
// Server texts shown to the officer arrive in Spanish; these give their English reading.
const MONTHS_EN = { ene: "Jan", feb: "Feb", mar: "Mar", abr: "Apr", may: "May", jun: "Jun", jul: "Jul", ago: "Aug", sep: "Sep", oct: "Oct", nov: "Nov", dic: "Dec" };
/** «26 abr» -> «26 Apr» on the English screen. */
const dtx = (txt) => (LANG === "es" || txt == null ? txt : String(txt).replace(/\b(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\b/g, (m) => MONTHS_EN[m]));
/** Owner names carry «(ficticio)» / «(ficticia)»: the demo roster is fictional. */
const own = (name) => (LANG === "es" ? String(name ?? "") : String(name ?? "").replace(/\(fictici[oa]\)/, "(fictional)"));
const CYCLE_EN = { corto: "Short cycle", intermedio: "Medium cycle", largo: "Long cycle" };
const cyc = (c) => (LANG === "es" ? c.label.split(" (")[0] : CYCLE_EN[c.id] || c.label.split(" (")[0]);
const cycById = (id, label) => (LANG === "es" ? String(label || id).split(" (")[0] : CYCLE_EN[id] || label || id);

const fmtT = (t) => (t == null ? "—" : (t > 0 ? "+" : t < 0 ? "−" : "") + Math.abs(t).toFixed(1) + " °C");
const pct = (p) => Math.round(p * 100) + "%";
const CROPS = { maiz_temporal: ["maíz", "maize"], papa: ["papa", "potato"], avena: ["avena", "oats"], haba: ["haba", "faba bean"] };
const crop = (c) => (CROPS[c] ? TX(...CROPS[c]) : c);
const SUPPORT = {
  "station-anchored": { short: ["Con estación", "Station nearby"], cls: "anch" },
  "terrain-transfer": { short: ["Sin estación", "No station"], cls: "transfer" },
};
const OUTCOMES = { hit: ["acierto", "hit"], miss: ["helada no detectada", "frost missed"], false_alarm: ["falsa alarma", "false alarm"],
  correct_negative: ["sin helada, sin alarma", "no frost, no alarm"] };
const outcome = (o) => (OUTCOMES[o] ? TX(...OUTCOMES[o]) : o || "—");
function supportInfo(s) {
  if (s && s.startsWith("logger-anchored")) { const k = (s.match(/\d+/) || ["?"])[0]; return { short: TX(`Termómetro (${k} noches)`, `Thermometer (${k} nights)`), cls: "logger" }; }
  return SUPPORT[s] ? { short: TX(...SUPPORT[s].short), cls: SUPPORT[s].cls } : undefined;
}
function supportOf(f) {
  if (f.support) return f.support;
  const d = f.drivers || {};
  if (d.support_station_anchored == null) return null;
  return d.support_station_anchored ? "station-anchored" : "terrain-transfer";
}
function supportTag(f) {
  const s = supportInfo(supportOf(f));
  return s ? `<span class="tag sup ${s.cls}">${s.short}</span>` : '<span class="muted">—</span>';
}

const state = { cfg: null, fc: null, sel: null, markers: {}, selRing: null, phone: null, lastMsgId: 0, parcels: [], alerts: [] };

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function frostColor(p) { return p >= 0.6 ? cssVar("--f3") : p >= 0.3 ? cssVar("--f2") : p >= 0.1 ? cssVar("--f1") : cssVar("--f0"); }
function demoNow() { const v = $("#demoNow").value; return v ? v : null; }

// The published dashboard runs on a single server instance: a request that arrives while another one is being
// served is turned away at once with 503 (or 429), before it reaches the app. Those are safe to send again, so wait
// a moment and repeat. A 503 that took seconds is a request that ran and timed out: it is not repeated.
async function fetchRetry(path, opts = {}) {
  for (let i = 0; ; i++) {
    const t0 = performance.now();
    const r = await fetch(path, opts);
    const turnedAway = (r.status === 503 || r.status === 429) && performance.now() - t0 < 3000;
    if (!turnedAway || i >= 8) return r;
    await new Promise((ok) => setTimeout(ok, 200 * (i + 1) + Math.random() * 250));
  }
}

async function api(path, opts = {}) {
  const r = await fetchRetry(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

/* ---------------- theme ---------------- */
function initTheme() {
  let t = null;
  try { t = localStorage.getItem("helada-theme"); } catch (e) { /* ignore */ }
  if (t) document.documentElement.dataset.theme = t;
  $("#themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const nx = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nx;
    try { localStorage.setItem("helada-theme", nx); } catch (e) { /* ignore */ }
    if (state.fc) renderForecast();
  };
}

/* ---------------- config chips ---------------- */
async function loadConfig() {
  state.cfg = await api("/api/config");
  renderChips();
  renderAsrHint();
}
function renderAsrHint() {
  $("#asrHint").textContent = state.cfg.asr === "mock"
    ? TX("Voz a texto simulada (sin WHISPER_MODEL): use «Audio de ejemplo» o escriba. Con faster-whisper, grabe su voz.",
        "Speech-to-text is simulated (no WHISPER_MODEL): use «Sample voice note» or type. With faster-whisper, record your voice.")
    : TX(`Voz a texto: ${state.cfg.asr}`, `Speech-to-text: ${state.cfg.asr}`);
}
function renderChips(modelSource) {
  const c = state.cfg;
  // Only what the officer has to know stays in the header: engines and channel are not shown.
  const chips = [];
  if ((modelSource || (state.fc && state.fc.model_source) || c.model_source) === "TEMP_STUB") chips.push(TX("Modelo: STUB temporal", "Model: temporary STUB"));
  if (!c.advisory_signed) chips.push(TX(esc(c.advisory_label || "Borrador sin firma agronómica"), "Draft: advice not yet signed by an agronomist"));
  if (c.offline) chips.push(TX("Sin conexión", "Offline"));
  $("#chips").innerHTML = chips.map((h) => `<span class="chip warn">${h}</span>`).join("");
}

/* ---------------- tabs ---------------- */
const TAB_LOAD = { paquetes: () => loadPackets(), bitacora: () => loadAudit(), evidencia: () => loadBacktest(),
  registrador: () => loadLogger(), calendario: () => loadCalendar() };
/** Show a tab and load what it needs. Returns when its content is on screen. */
function openTab(name) {
  $$(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "mapa") setTimeout(() => map.invalidateSize(), 50);
  return Promise.resolve(TAB_LOAD[name] ? TAB_LOAD[name]() : null);
}
function initTabs() {
  $$(".tab").forEach((b) => (b.onclick = () => openTab(b.dataset.tab)));
}

/* ---------------- map + forecast ---------------- */
let map;
function initMap() {
  // The wheel over the map scrolls the page. It zooms only after a click on the map (until the pointer leaves),
  // or with Ctrl/⌘ held, which is also what a trackpad pinch sends. The + / − buttons and double click stay.
  map = L.map("map", { zoomControl: true, scrollWheelZoom: false }).setView([19.5, -99.8], 10);
  const box = map.getContainer();
  let wheel = 0;
  box.addEventListener("click", () => map.scrollWheelZoom.enable());
  box.addEventListener("mouseleave", () => { map.scrollWheelZoom.disable(); wheel = 0; });
  box.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey) || map.scrollWheelZoom.enabled()) return;
    e.preventDefault();
    wheel += e.deltaY;
    if (Math.abs(wheel) < 40) return;
    map.setZoomAround(map.mouseEventToContainerPoint(e), map.getZoom() + (wheel < 0 ? 1 : -1));
    wheel = 0;
  }, { passive: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 17, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  renderLegend();
  // the map is as tall as the parcel card next to it
  if (window.ResizeObserver) new ResizeObserver(() => map.invalidateSize()).observe($("#map"));
}
function renderLegend() {
  $("#legend").innerHTML = [["--f0", "< 10%"], ["--f1", "10–30%"], ["--f2", "30–60%"], ["--f3", "≥ 60%"]]
    .map(([v, t]) => `<span><span class="sw" style="background:var(${v})"></span>${t}</span>`).join("") +
    `<span>· ${TX("color = probabilidad de helada en la parcela", "colour = chance of frost on the plot")}</span>` +
    `<span><span class="sw dashed"></span>${TX("sin estación cerca: cálculo aproximado", "no station nearby: approximate estimate")}</span>` +
    `<span class="muted">· ${TX("para acercar con la rueda: clic en el mapa, o Ctrl + rueda", "to zoom with the wheel: click the map, or Ctrl + wheel")}</span>`;
}

async function loadForecast() {
  const d = $("#nightDate").value;
  const sc = $("#scenario").value;
  $("#refreshBtn").disabled = true;
  try {
    const q = new URLSearchParams({ date: d });
    if (sc) q.set("scenario", sc);
    const [fc, alerts] = await Promise.all([api("/api/forecast?" + q), api("/api/alerts")]);
    state.fc = fc; state.alerts = alerts;
    renderChips(fc.model_source);
    renderForecast();
  } catch (e) { toast(TX("No se pudo cargar el pronóstico: ", "Could not load the forecast: ") + e.message); }
  finally { $("#refreshBtn").disabled = false; }
}

function lastAlert(pid) { return state.alerts.find((a) => a.parcel_id === pid && a.status !== "expired"); }

function obsCell(r, fc) {
  if (r.truth && r.truth.observed_tmin_c != null) return fmtT(r.truth.observed_tmin_c);
  if (fc.scenario === "demo") return `<span class="muted">${TX("sin estación", "no station")}</span>`;
  const o = r.observed;
  if (!o) return `<span class="muted" title="${TX("Sin estación SMN a menos de 1.5 km", "No SMN station within 1.5 km")}">${TX("sin estación", "no station")}</span>`;
  if (o.status === "ok") return `<span title="SMN ${esc(o.station)} ${esc(o.station_name)}, ${TX("lectura 08:00 del", "08:00 reading of")} ${esc(o.obs_date)}">${fmtT(o.tmin_c)}</span>`;
  return `<span class="muted" title="${esc(o.text || "")}">${o.status === "not_published" ? TX("aún no publicado", "not published yet") : TX("sin dato", "no data")}</span>`;
}

function renderForecast() {
  const fc = state.fc;
  const b = $("#scenarioBanner");
  const srcs = fc.grid_sources.join(", ");
  if (fc.scenario === "demo" && fc.replay) {
    const rp = fc.replay, ns = rp.night_summary || {};
    b.hidden = false;
    b.innerHTML = TX(`<b>Noche real:</b> pronóstico archivado del día anterior y la temperatura que midieron las estaciones del SMN. ` +
      `De ${ns.frost_with_forecast_above_1c} heladas que el pronóstico regional no anunciaba, Helada avisó ${ns.caught_p_ge_0_3}, con ${ns.false_alarms_p_ge_0_3} falsa alarma. ` +
      `<span class="dev-only muted">${esc(rp.label)}. ${esc(fc.model_variant_label || "")}. Open-Meteo ecmwf_ifs025, corrida del día anterior. ${esc(rp.holdout_note)} Productores ficticios.</span>`,
      `<b>A real night:</b> the forecast archived the day before, and the temperature the national weather service (SMN) stations measured. ` +
      `Of ${ns.frost_with_forecast_above_1c} frosts the regional forecast did not announce, Helada alerted ${ns.caught_p_ge_0_3}, with ${ns.false_alarms_p_ge_0_3} false alarm${ns.false_alarms_p_ge_0_3 === 1 ? "" : "s"}. ` +
      `<span class="dev-only muted">Replay of the night of 14–15 November 2025. The model shown was trained without the 2025-26 season (variant holdout_2025_26), so it never saw this night. Open-Meteo ecmwf_ifs025, previous day's run. Fictional farmers.</span>`);
    $("#nightDate").value = fc.date;
    if (!demoNow()) $("#demoNow").value = fc.date + "T18:30";
  }
  else if (fc.unsure_reason === "sin_pronostico") { b.hidden = false; b.innerHTML = TX("<b>Sin conexión y sin pronóstico guardado para esta noche.</b> Las cifras de la tabla salen del promedio del mes, no de un pronóstico: no se manda ningún aviso con cifras. «Enviar avisos» manda «No estoy seguro, pregunte a su técnico».",
    "<b>Offline, and no forecast saved for this night.</b> The numbers in the table come from the monthly average, not from a forecast: no alert with numbers is sent. «Send alerts» sends «No estoy seguro, pregunte a su técnico» (I'm not sure, ask your extension officer)."); }
  else if (srcs.includes("guardado")) { b.hidden = false; b.innerHTML = TX(`<b>Sin conexión: se usa el último pronóstico guardado.</b> <span class="muted">${esc(srcs)}</span>`,
    `<b>Offline: using the last saved forecast.</b> <span class="muted">${esc(srcs.replace(/pronóstico guardado/g, "saved forecast"))}</span>`); }
  else b.hidden = true;
  Object.values(state.markers).forEach((m) => m.remove());
  state.markers = {};
  const tb = $("#fcTable tbody");
  tb.innerHTML = "";
  // highest chance of frost first: the rows that need attention tonight are at the top
  const ranked = [...fc.rows].sort((a, b) => b.forecast.p_frost - a.forecast.p_frost || a.parcel.parcel_id.localeCompare(b.parcel.parcel_id));
  const nAlert = ranked.filter((r) => r.level === "alert").length, nWatch = ranked.filter((r) => r.level === "watch").length;
  $("#nightSummary").innerHTML = TX(`<b>${nAlert}</b> ${nAlert === 1 ? "parcela con aviso" : "parcelas con aviso"}` +
    (nWatch ? ` · <b>${nWatch}</b> en vigilancia` : "") + ` · ${ranked.length - nAlert - nWatch} sin riesgo. Ordenadas de mayor a menor probabilidad de helada.`,
    `<b>${nAlert}</b> ${nAlert === 1 ? "plot to alert" : "plots to alert"}` +
    (nWatch ? ` · <b>${nWatch}</b> on watch` : "") + ` · ${ranked.length - nAlert - nWatch} with no risk. Sorted from highest to lowest chance of frost.`);
  for (const r of ranked) {
    const p = r.parcel, f = r.forecast;
    const col = frostColor(f.p_frost);
    const transfer = supportOf(f) === "terrain-transfer";
    const m = L.circleMarker([p.lat, p.lon], { radius: 12, color: transfer ? cssVar("--warn") || "#c60" : "#fff", weight: transfer ? 3 : 2.5,
      dashArray: transfer ? "4 3" : null, fillColor: col, fillOpacity: 0.95 })
      .addTo(map).bindTooltip(`${p.parcel_id} · ${esc(own(p.owner_name))}<br>${TX("Su parcela", "On the plot")} ${fmtT(f.tmin_c)} · P ${pct(f.p_frost)}${transfer ? "<br>" + TX("Transferencia por terreno: rango amplio", "Terrain transfer: wide range") : ""}`);
    m.on("click", () => select(p.parcel_id));
    state.markers[p.parcel_id] = m;
    const la = lastAlert(p.parcel_id);
    const tr = document.createElement("tr");
    tr.dataset.pid = p.parcel_id;
    tr.innerHTML = `<td><span class="dot" style="background:${col}"></span>${p.parcel_id}</td>
      <td>${esc(own(p.owner_name))}<div class="sub">${esc(p.municipality)} · ${crop(p.crop)}</div></td>
      <td class="num">${fmtT(f.grid_tmin_c)}</td><td class="num"><b>${fmtT(f.tmin_c)}</b></td>
      <td class="num">${fmtT(f.tmin_lo_c)} ${TX("a", "to")} ${fmtT(f.tmin_hi_c)}</td>
      <td><span class="pmini"><span class="bar"><i style="width:${Math.max(3, f.p_frost * 100)}%;background:${col}"></i></span><b>${pct(f.p_frost)}</b></span></td>
      <td>${supportTag(f)}</td>
      <td class="num">${obsCell(r, fc)}</td>
      <td>${ruleCell(r)}</td>
      <td>${la ? esc(la.status === "sent" ? (la.level === "unsure" ? TX("«no estoy seguro» ", "«not sure» ") : TX("enviado ", "sent ")) + (la.sent_at || "").slice(5, 16).replace("T", " ") : la.status) : "—"}</td>`;
    tr.onclick = () => select(p.parcel_id);
    tb.appendChild(tr);
  }
  if (!state.sel && fc.rows.length) {
    const top = [...fc.rows].sort((a, b) => b.forecast.p_frost - a.forecast.p_frost)[0];
    select(fc.rows.find((r) => r.parcel.parcel_id === "P01") ? "P01" : top.parcel.parcel_id, false);
  } else if (state.sel) select(state.sel, false);
}

/** Mark the selected parcel on the map: a ring around its point, the point above its neighbours and its id
 *  always in view (the id alone: a longer label hides the points next to it). The point keeps its own style,
 *  so the frost colour and the dashed border still read. */
function markSelected(r) {
  if (state.selRing) { state.selRing.remove(); state.selRing = null; }
  const m = r && state.markers[r.parcel.parcel_id];
  if (!m) return;
  const p = r.parcel;
  state.selRing = L.circleMarker([p.lat, p.lon], { radius: 19, weight: 4, fill: false, interactive: false, className: "sel-ring" })
    .addTo(map).bindTooltip(esc(p.parcel_id), { permanent: true, direction: "top", offset: [0, -20], className: "sel-tip" });
  m.bringToFront();
}

async function select(pid, pan = true) {
  state.sel = pid;
  $$("#fcTable tbody tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.pid === pid));
  const r = state.fc.rows.find((x) => x.parcel.parcel_id === pid);
  markSelected(r);
  if (!r) return;
  const p = r.parcel, f = r.forecast, d = f.drivers || {};
  setPhone(p.phone);
  if (pan) map.panTo([p.lat, p.lon]);
  const col = frostColor(f.p_frost);
  const stub = state.fc.model_source === "TEMP_STUB";
  const unsure = state.fc.unsure_reason;
  const la = lastAlert(pid), sms = la && la.status === "sent" && la.night_date === f.date ? la.sms_text : null;
  const sup = supportOf(f);
  const km = d.nearest_station_km != null ? Number(d.nearest_station_km).toFixed(1) : "?";
  const sid = d.nearest_station_id != null ? String(Math.round(d.nearest_station_id)) : "";
  const stName = r.truth && r.truth.station ? `${esc(r.truth.station.name)} (${esc(r.truth.station.id)})` : sid;
  const sgn = (v, u) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v * 10) / 10)} ${u}`;
  const drv = [];   // human rows, always visible
  const yn = (v) => (v ? TX("sí", "yes") : "no");
  if (d.elev_diff_m != null) drv.push([TX("Altura vs. celda del pronóstico", "Height vs. the forecast grid cell"), sgn(d.elev_diff_m, "m")]);
  if (d.tpi != null) drv.push([TX("Posición en el terreno (TPI)", "Position in the terrain (TPI)"), `${sgn(d.tpi, "m")}${d.tpi <= -5 ? TX(" · hondonada", " · hollow") : d.tpi >= 5 ? TX(" · loma", " · rise") : TX(" · ladera / plano", " · slope / flat")}`]);
  if (d.clear_sky != null) drv.push([TX("Cielo despejado", "Clear sky"), pct(d.clear_sky)]);
  if (sup === "station-anchored" && sid) drv.push([TX("Estación de referencia", "Reference station"), `${stName}, ${km} km`]);
  const TECH = {
    support_station_anchored: [TX("Anclada a estación", "Station-anchored"), yn],
    nearest_station_id: [TX("Estación más cercana (id)", "Nearest station (id)"), (v) => Math.round(v)],
    nearest_station_km: [TX("Distancia a la estación", "Distance to the station"), (v) => `${v} km`],
    station_offset_c: [TX("Ajuste propio de la estación", "The station's own offset"), (v) => sgn(v, "°C")],
    elev_m: [TX("Altitud de la parcela", "Plot elevation"), (v) => `${v} m`],
    dem_elev_m: [TX("Altitud en el modelo de terreno (DEM)", "Elevation in the terrain model (DEM)"), (v) => `${v} m`],
    tpi_3k: [TX("TPI a 3 km", "TPI at 3 km"), (v) => sgn(v, "m")],
    height_above_valley_floor_m: [TX("Altura sobre el fondo del valle", "Height above the valley floor"), (v) => `${v} m`],
    slope_deg: [TX("Pendiente", "Slope"), (v) => `${v}°`],
    northness: [TX("Orientación norte (−1 sur, +1 norte)", "Northness (−1 south, +1 north)"), (v) => v],
    horizon_deg: [TX("Horizonte (obstrucción del cielo)", "Horizon (sky obstruction)"), (v) => `${v}°`],
    wind_kmh: [TX("Viento", "Wind"), (v) => `${v} km/h`],
    wind_ms: [TX("Viento", "Wind"), (v) => `${v} m/s`],
    dewpoint_depression_c: [TX("Diferencia temperatura-punto de rocío", "Temperature minus dew point"), (v) => `${v} °C`],
    clear_calm: [TX("Índice cielo despejado y calma", "Clear-and-calm index"), (v) => v],
    correction_c: [TX("Corrección del modelo sobre el pronóstico", "Model correction to the forecast"), (v) => sgn(v, "°C")],
    lapse_adj_c: [TX("Ajuste por altura (gradiente 6.5 °C/km)", "Height adjustment (lapse rate 6.5 °C/km)"), (v) => sgn(v, "°C")],
    forecast_fallback: [TX("Datos del pronóstico faltantes (rellenados)", "Missing forecast inputs (filled in)"), (v) => Math.round(v)],
    variant_holdout_2025_26: [TX("Modelo sin la temporada 2025-26", "Model without the 2025-26 season"), yn],
  };
  const shown = new Set(["elev_diff_m", "tpi", "clear_sky"]);
  const tech = drv.concat(Object.entries(d).filter(([k]) => !shown.has(k)).map(([k, v]) => {
    const t = TECH[k];
    return [t ? t[0] : k, typeof v === "number" ? (t ? t[1](v) : v) : esc(v)];
  }));
  // the differentiator, in plain words: how far the plot is from the regional forecast, and why
  const gap = f.tmin_c - f.grid_tmin_c, gapAbs = Math.abs(Math.round(gap * 10) / 10);
  const why = sup === "station-anchored" ? TX("La estación que está junto a la parcela registra noches distintas a las del pronóstico regional, y el modelo aprendió esa diferencia.",
      "The station next to the plot records nights that differ from the regional forecast, and the model learned that difference.")
    : sup && String(sup).startsWith("logger") ? TX("El modelo se ajustó con las lecturas del termómetro de la parcela.", "The model was adjusted with the readings of the thermometer on the plot.")
    : TX("No hay estación cerca: es un cálculo aproximado, con rango amplio.", "There is no station nearby: this is an approximate estimate, with a wide range.");
  const diffHtml = gapAbs >= 0.5
    ? `<p class="diff"><b>${TX(`Su parcela: ${gapAbs} °C más ${gap < 0 ? "fría" : "cálida"} que el pronóstico regional.`, `The plot: ${gapAbs} °C ${gap < 0 ? "colder" : "warmer"} than the regional forecast.`)}</b><br>${why}</p>`
    : `<p class="diff"><b>${TX("Su parcela: casi igual al pronóstico regional.", "The plot: about the same as the regional forecast.")}</b><br>${why}</p>`;
  let supHtml = "";
  if (sup === "station-anchored") supHtml = `<div class="support anch"><b>${TX(`Estación SMN ${stName} a ${km} km.`, `Weather-service (SMN) station ${stName}, ${km} km away.`)}</b>
    <span class="dev-only">${TX("Régimen validado: en la temporada 2025-26 (no vista) el error de Tmin bajó de 2.65 a 1.40 °C y se detectó 58% de las noches con helada (vs. 24%) con 5% de falsas alarmas.",
      "Validated setting: in the 2025-26 season (not seen in training) the Tmin error fell from 2.65 to 1.40 °C, and 58% of frost nights were detected (vs. 24%) at 5% false alarms.")}</span></div>`;
  else if (sup === "terrain-transfer") supHtml = `<div class="support transfer"><b>${TX("Sin estación a menos de 1.5 km: cálculo aproximado.", "No station within 1.5 km: approximate estimate.")}</b>
    ${TX("Un termómetro en la parcela durante una temporada lo vuelve preciso.", "A thermometer on the plot for one season makes it precise.")}
    <span class="dev-only">${TX(`Aquí el modelo solo quita el sesgo cálido del pronóstico; no detecta heladas mejor que el pronóstico y el rango es amplio (${fmtT(f.tmin_lo_c)} a ${fmtT(f.tmin_hi_c)}). Registrador: ~US$30–60.`,
      `Here the model only removes the forecast's warm bias; it does not detect frost better than the forecast, and the range is wide (${fmtT(f.tmin_lo_c)} to ${fmtT(f.tmin_hi_c)}). Logger: ~US$30–60.`)}</span></div>`;
  else if (stub) supHtml = `<div class="support transfer">${TX("STUB temporal: sin validación ni régimen de soporte.", "Temporary STUB: not validated, no support setting.")}</div>`;
  let truthHtml = "";
  if (state.fc.scenario === "demo") {
    const t = r.truth;
    if (t && t.observed_tmin_c != null) {
      const h = t.holdout || {};
      truthHtml = `<div class="truth"><div class="row"><span>${TX(`Observado esa noche (SMN ${stName}, 08:00)`, `Measured that night (SMN ${stName}, 08:00)`)}</span><b>${fmtT(t.observed_tmin_c)}</b></div>
        <div class="row"><span>${TX("Resultado (modelo sin la temporada 2025-26)", "Outcome (model trained without the 2025-26 season)")}</span><b>${esc(outcome(h.outcome))}</b></div></div>`;
    } else truthHtml = `<div class="truth"><div class="row"><span>${TX("Observado esa noche", "Measured that night")}</span><b>${TX("sin estación: no se sabe", "no station: unknown")}</b></div></div>`;
  }
  $("#compare").innerHTML = `
    <h3>${p.parcel_id} · ${esc(own(p.owner_name))}</h3>
    <p class="who">${esc(p.municipality)} · ${p.area_ha} ha · ${crop(p.crop)} · ${Math.round(p.elev_m)} m
      <span class="tag ${stub ? "stub" : "dev-only"}">${stub ? TX("STUB temporal", "temporary STUB") : "helada_model"}</span> ${supportTag(f)}</p>
    ${supHtml}
    <div class="vs">
      <div class="box"><div class="lbl">${TX("Pronóstico regional", "Regional forecast")}</div><div class="big">${fmtT(f.grid_tmin_c)}</div><div class="small">${TX("lo que anuncia el pronóstico para la zona", "what the forecast announces for the area")}</div></div>
      <div class="box parcel"><div class="lbl">${TX("Su parcela", "On the plot")}</div><div class="big" style="color:${col}">${fmtT(f.tmin_c)}</div><div class="small">${TX("entre", "between")} ${fmtT(f.tmin_lo_c)} ${TX("y", "and")} ${fmtT(f.tmin_hi_c)}</div></div>
    </div>
    ${diffHtml}
    <div class="pbar"><div class="row"><span>${TX("Probabilidad de helada", "Chance of frost")}</span><span>${pct(f.p_frost)}</span></div>
      <div class="track"><div class="fill" style="width:${Math.max(2, f.p_frost * 100)}%;background:${col}"></div></div></div>
    ${truthHtml}
    <div>${unsure ? `<span class="rule-watch">${unsureWhy(unsure)}${TX(": no se manda aviso ni cifras. El productor recibe «No estoy seguro, pregunte a su técnico».", ": no alert and no number is sent. The farmer gets «No estoy seguro, pregunte a su técnico» (I'm not sure, ask your extension officer).")}</span>`
      : r.level === "alert" ? `<span class="rule-yes">${TX("Se avisa al productor: probabilidad de 30% o más", "The farmer is alerted: the chance is 30% or more")}</span>`
      : r.level === "watch" ? `<span class="rule-watch">${TX("Vigilancia: sin estación cerca y el rango llega a 0 °C. Se manda un mensaje, no un aviso.", "Watch: no station nearby and the range reaches 0 °C. A message is sent, not an alert.")}</span>`
      : `<span class="muted">${TX("Sin aviso: probabilidad menor a 30%", "No alert: the chance is under 30%")}</span>`}</div>
    ${sms ? `<div class="sms-line"><span class="muted">${TX(`En un teléfono básico el mismo aviso llega por SMS (${sms.length} de 160 caracteres, sin acentos):`, `On a basic phone the same alert arrives as an SMS (${sms.length} of 160 characters, no accents; in Spanish):`)}</span><code>${esc(sms)}</code></div>` : ""}
    <details class="tech"><summary>${TX("Detalles técnicos", "Technical details")}</summary>
      <ul class="drivers">${tech.map(([k, v]) => `<li><span>${esc(k)}</span><b>${v}</b></li>`).join("")}</ul></details>
    <div class="clim" id="clim"></div>
    <div class="plant" id="plant"></div>`;
  loadPlanting(pid);
  try {
    const c = await api("/api/climatology/" + pid);
    if (state.sel !== pid || !$("#clim")) return;
    const doy = (n) => { const dt = new Date(Date.UTC(2026, 0, 1) + (n - 1) * 864e5); return dt.toLocaleDateString(LOCALE(), { day: "numeric", month: "short", timeZone: "UTC" }); };
    $("#clim").innerHTML = TX(`Climatología de la parcela${c.source === "TEMP_STUB" ? " (STUB)" : ""}: última helada de primavera ~${doy(c.last_spring_frost_doy_p50)} (9 de 10 años antes del ${doy(c.last_spring_frost_doy_p90)}); primera de otoño ~${doy(c.first_autumn_frost_doy_p50)}; ${c.frost_free_days_p50} días libres de helada.`,
      `Frost climatology of the plot${c.source === "TEMP_STUB" ? " (STUB)" : ""}: last spring frost ~${doy(c.last_spring_frost_doy_p50)} (9 years in 10 before ${doy(c.last_spring_frost_doy_p90)}); first autumn frost ~${doy(c.first_autumn_frost_doy_p50)}; ${c.frost_free_days_p50} frost-free days.`);
  } catch (e) { /* optional */ }
}

/* «Calendario de siembra»: sowing windows per maize cycle from the parcel's frost climatology (deterministic).
   The parcel card carries one line and a way in; the calendar itself is its own tab. */
const CAL = { sow: null, data: {} };
async function plantingOf(pid) { return CAL.data[pid] || (CAL.data[pid] = await api("/api/planting/" + pid)); }
async function loadPlanting(pid) {
  let a;
  try { a = await plantingOf(pid); } catch (e) { return; }
  const el = $("#plant");
  if (!el || state.sel !== pid) return;
  const c = a.cycles[0];
  el.innerHTML = `<div class="plant-teaser"><div><b>${TX("Calendario de siembra", "Sowing calendar")}</b> · ${TX("maíz de temporal", "rainfed maize")}
      <div class="muted">${TX(`${esc(cyc(c))}: sembrar a más tardar el <b>${esc(c.latest_p10_txt)}</b> para que madure antes de la helada.`, `${esc(cyc(c))}: sow by <b>${esc(dtx(c.latest_p10_txt))}</b> at the latest so it matures before the frost.`)}</div></div>
    <button class="btn" type="button">${TX("Ver calendario", "See the calendar")}</button></div>`;
  el.querySelector("button").onclick = () => openTab("calendario");
  if ($("#tab-calendario").classList.contains("active")) loadCalendar();
}

// Chance of frost before the crop matures, in four steps. Never colour alone: each use carries the words.
const RISK = [{ max: 0.15, cls: "r0", es: "Riesgo bajo", en: "Low risk" }, { max: 0.35, cls: "r1", es: "Riesgo moderado", en: "Moderate risk" },
  { max: 0.6, cls: "r2", es: "Riesgo alto", en: "High risk" }, { max: Infinity, cls: "r3", es: "Riesgo muy alto", en: "Very high risk" }];
const riskOf = (p) => { const r = RISK.find((k) => p < k.max); return { ...r, label: TX(r.es, r.en) }; };
const de10 = (p) => (p < 0.05 ? TX("menos de 1 de cada 10 años", "fewer than 1 year in 10") : p >= 0.95 ? TX("casi todos los años", "almost every year")
  : TX(`${Math.round(p * 10)} de cada 10 años`, `${Math.round(p * 10)} years in 10`));
// English reading of the sowing advice, which the server writes in Spanish for the farmer.
const WHEN_EN = { humedad_residual: "on residual moisture or with a first irrigation (mid-April)", temporal: "with the rains (early June)" };
function basisText(b) {
  if (LANG === "es") return b;
  let m = /^estación SMN (.+), n\.º (\d+), a ([\d.]+) km de su parcela; registros (.+)$/.exec(b);
  if (m) return `SMN station ${m[1]}, no. ${m[2]}, ${m[3]} km from the plot; records ${m[4]}`;
  m = /^sin estación a menos de 1\.5 km: se usa la fecha más temprana entre la estimación por terreno y la estación SMN (.+), n\.º (\d+), a ([\d.]+) km \(aproximado\)$/.exec(b);
  if (m) return `no station within 1.5 km: the earlier date between the terrain estimate and SMN station ${m[1]}, no. ${m[2]}, ${m[3]} km away (approximate)`;
  return b;
}
function recLines(a) {
  if (LANG === "es" || !a.recommendation) return a.recommendation_lines;
  return Object.entries(a.recommendation).map(([k, r]) => {
    const when = WHEN_EN[k] || r.label, c = cycById(r.cycle, r.cycle_label).toLowerCase();
    if (r.status === "ok") return `If sowing ${when}: ${c}, by ${dtx(r.latest_txt)} at the latest` +
      (r.alt ? `; the ${cycById(r.alt.cycle, r.alt.cycle_label).toLowerCase()} only if sowing before ${dtx(r.alt.latest_txt)}.` : ".");
    return `If sowing ${when}: no cycle matures safely before the early frost. With the ${c}, frost would come before it matures about ${de10(r.p_frost_before_maturity)}` +
      ` (in full flowering: ${de10(r.p_frost_flowering)}). The farmer is told to ask the extension officer for the earliest variety.`;
  });
}
const doyOf = (md) => Math.round((Date.UTC(2026, +md.slice(0, 2) - 1, +md.slice(3)) - Date.UTC(2026, 0, 1)) / 864e5) + 1;

async function loadCalendar() {
  const box = $("#calendar"), pid = state.sel;
  if (!pid || !state.fc) return;
  let a;
  try { a = await plantingOf(pid); } catch (e) { box.innerHTML = `<p class="bad">${TX("No se pudo cargar el calendario:", "Could not load the calendar:")} ${esc(e.message)}</p>`; return; }
  if (state.sel !== pid) return;
  if (!a.table.some((r) => r.sow === CAL.sow)) CAL.sow = (a.table.find((r) => r.sow === "04-15") || a.table[0]).sow;
  renderCalendar(a);
}

/* One time axis (March to December). Shaded: when frost is still or already likely on this parcel.
   One bar per maize cycle, from the chosen sowing day to maturity; the ring marks flowering. */
function calendarChart(a, row) {
  const f = a.frost, D0 = 60, D1 = 365, W = 920, LX = 132, R = 14, T = 54, RH = 50, n = a.cycles.length, H = T + n * RH + 26;
  const x = (d) => LX + ((Math.min(Math.max(d, D0), D1) - D0) / (D1 - D0)) * (W - LX - R);
  const MES = ["mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"].map(dtx);
  let g = "";
  const sp50 = doyOf(f.last_spring_p50), sp90 = doyOf(f.last_spring_p90), au10 = doyOf(f.first_autumn_p10), au50 = doyOf(f.first_autumn_p50);
  const zone = (d0, d1, cls) => (d1 > d0 ? `<rect class="cz ${cls}" x="${x(d0)}" y="${T - 6}" width="${x(d1) - x(d0)}" height="${n * RH + 6}"/>` : "");
  g += zone(D0, sp90, "some") + zone(D0, sp50, "most") + zone(au10, D1, "some") + zone(au50, D1, "most");
  MES.forEach((m, i) => {
    const d = doyOf(String(i + 3).padStart(2, "0") + "-01"), d2 = i < 9 ? doyOf(String(i + 4).padStart(2, "0") + "-01") : D1;
    g += `<line class="cg" x1="${x(d)}" y1="${T - 6}" x2="${x(d)}" y2="${T + n * RH}"/><text class="cm" x="${(x(d) + x(d2)) / 2}" y="${T + n * RH + 18}">${m}</text>`;
  });
  if (sp90 > D0 + 12) g += `<text class="czl" x="${x(D0) + 6}" y="${T - 14}">${TX("Heladas de primavera", "Spring frosts")}</text>`;
  g += `<text class="czl end" x="${W - R - 6}" y="${T - 14}">${TX("Heladas de otoño", "Autumn frosts")}</text>`;
  g += `<line class="csow" x1="${x(doyOf(row.sow))}" y1="${T - 34}" x2="${x(doyOf(row.sow))}" y2="${T + n * RH}"/>` +
       `<text class="csl" x="${x(doyOf(row.sow))}" y="${T - 40}">${TX("siembra", "sowing")} ${esc(dtx(row.sow_txt))}</text>`;
  a.cycles.forEach((c, i) => {
    const cell = row.cells.find((k) => k.cycle === c.id) || row.cells[i], rk = riskOf(cell.p_frost_before_maturity);
    const y = T + i * RH + RH / 2 + 6, x0 = x(doyOf(row.sow)), x1 = x(doyOf(cell.maturity)), xf = x(doyOf(cell.flowering));
    g += `<text class="cn" x="${LX - 12}" y="${y - 2}">${esc(cyc(c))}</text><text class="cd" x="${LX - 12}" y="${y + 13}">${c.maturity_days} ${TX("días", "days")}</text>` +
         `<rect class="cb ${rk.cls}" x="${x0}" y="${y - 7}" width="${Math.max(4, x1 - x0)}" height="14" rx="4"/>` +
         `<circle class="cf" cx="${xf}" cy="${y}" r="5"/>` +
         `<text class="ct${x1 > W - 190 ? " end" : ""}" x="${x1 > W - 190 ? x1 : x0 + 7}" y="${y - 13}">${TX("flor", "flowers")} ${esc(dtx(cell.flowering_txt))} · ${TX("madura", "matures")} ${esc(dtx(cell.maturity_txt))}</text>` +
         `<rect class="nc-hit" x="0" y="${T + i * RH}" width="${W}" height="${RH}" data-tip="${esc(TX(`${cyc(c)}: siembra ${row.sow_txt}, flor ${cell.flowering_txt}, madura ${cell.maturity_txt}. Helada antes de madurar: ${de10(cell.p_frost_before_maturity)} (${rk.label.toLowerCase()}). En floración: ${de10(cell.p_frost_flowering)}.`,
           `${cyc(c)}: sown ${dtx(row.sow_txt)}, flowers ${dtx(cell.flowering_txt)}, matures ${dtx(cell.maturity_txt)}. Frost before maturity: ${de10(cell.p_frost_before_maturity)} (${rk.label.toLowerCase()}). At flowering: ${de10(cell.p_frost_flowering)}.`))}"/>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(TX(`Calendario: siembra el ${row.sow_txt}, por ciclo de maíz, contra las fechas de helada de la parcela`, `Calendar: sowing on ${dtx(row.sow_txt)}, by maize cycle, against the plot's frost dates`))}">${g}</svg>`;
}

function renderCalendar(a) {
  const f = a.frost, row = a.table.find((r) => r.sow === CAL.sow);
  const opts = state.fc.rows.map((r) => r.parcel).sort((p, q) => p.parcel_id.localeCompare(q.parcel_id))
    .map((p) => `<option value="${p.parcel_id}"${p.parcel_id === a.parcel_id ? " selected" : ""}>${p.parcel_id} · ${esc(own(p.owner_name))} · ${esc(p.municipality)}</option>`).join("");
  const cards = a.cycles.map((c, i) => {
    const cell = row.cells.find((k) => k.cycle === c.id) || row.cells[i], rk = riskOf(cell.p_frost_before_maturity);
    const ex = LANG === "en" && c.id === "largo" ? "local landraces of the valley (Cónico, Cacahuacintle, Chalqueño)" : c.examples;
    return `<div class="cal-card ${rk.cls}"><h4>${esc(cyc(c))}</h4><div class="ex">${esc(ex)}</div>
      <span class="risk-pill ${rk.cls}"><i></i>${rk.label}</span>
      <p>${TX("Helada antes de madurar:", "Frost before it matures:")} <b>${de10(cell.p_frost_before_maturity)}</b>.</p>
      <p class="muted">${TX(`Florece hacia el ${esc(cell.flowering_txt)} y madura hacia el ${esc(cell.maturity_txt)}`, `Flowers around ${esc(dtx(cell.flowering_txt))} and matures around ${esc(dtx(cell.maturity_txt))}`)}${c.verify ? " *" : ""}.</p>
      <p class="muted">${TX(`Para un riesgo de 1 de cada 10 años: sembrar a más tardar el <b>${esc(c.latest_p10_txt)}</b>.`, `For a risk of 1 year in 10: sow by <b>${esc(dtx(c.latest_p10_txt))}</b> at the latest.`)}</p></div>`;
  }).join("");
  const rk10 = (p) => (p < 0.05 ? "&lt;1/10" : p >= 0.95 ? "≈10/10" : Math.round(p * 10) + "/10");
  const trs = a.table.map((r) => `<tr data-sow="${r.sow}" class="${r.sow === CAL.sow ? "sel" : ""}"><td>${esc(dtx(r.sow_txt))}</td>${r.cells.map((c) =>
    `<td><span class="risk-pill ${riskOf(c.p_frost_before_maturity).cls}"><i></i>${rk10(c.p_frost_before_maturity)}</span> <span class="muted">${TX("flor", "flowers")} ${esc(dtx(c.flowering_txt))} · ${TX("madura", "matures")} ${esc(dtx(c.maturity_txt))}</span></td>`).join("")}</tr>`).join("");
  $("#calendar").innerHTML = `
    <div class="cal-head">
      <label>${TX("Parcela", "Plot")} <select id="calParcel">${opts}</select></label>
      <span class="muted">${TX("maíz de temporal", "rainfed maize")} · ${esc(basisText(a.basis))}</span>
      ${a.signed ? "" : `<span class="tag unsigned">${TX("Requiere firma agronómica", "Needs an agronomist's sign-off")}</span>`}${a.climatology_source === "TEMP_STUB" ? ' <span class="tag stub">STUB</span>' : ""}
    </div>
    <div class="cal-kpis">
      <div><span class="l">${TX("Última helada de primavera", "Last spring frost")}</span><span class="k">${esc(dtx(f.last_spring_p50_txt))}</span><span class="l">${TX("9 de cada 10 años, antes del", "9 years in 10, before")} ${esc(dtx(f.last_spring_p90_txt))}</span></div>
      <div><span class="l">${TX("Primera helada de otoño", "First autumn frost")}</span><span class="k">${esc(dtx(f.first_autumn_p50_txt))}</span><span class="l">${TX("1 de cada 10 años, antes del", "1 year in 10, before")} ${esc(dtx(f.first_autumn_p10_txt))}</span></div>
      <div><span class="l">${TX("Temporada sin heladas", "Frost-free season")}</span><span class="k">${f.frost_free_days} ${TX("días", "days")}</span><span class="l">${TX("entre una y otra, un año típico", "between the two, in a typical year")}</span></div>
    </div>
    <div class="cal-pick"><span class="q">${TX("¿Cuándo piensa sembrar?", "When will the farmer sow?")}</span>
      <div class="cal-days" role="group" aria-label="${TX("Fecha de siembra", "Sowing date")}">${a.table.map((r) => `<button type="button" class="cal-day" data-sow="${r.sow}" aria-pressed="${r.sow === CAL.sow}"><b>${esc(r.sow_txt.split(" ")[0])}</b><span>${esc(dtx(r.sow_txt.split(" ")[1] || ""))}</span></button>`).join("")}</div>
    </div>
    <div class="cal-legend">${RISK.map((r) => `<span><i class="sw ${r.cls}"></i>${TX(r.es, r.en)}</span>`).join("")}<span><i class="sw flor"></i>${TX("floración", "flowering")}</span><span><i class="sw zona"></i>${TX("época de heladas (más oscuro: la mitad de los años o más)", "frost season (darker: half of the years or more)")}</span></div>
    <div class="cal-chart nc-plot">${calendarChart(a, row)}<div class="nc-tip" hidden></div></div>
    <div class="cal-cards">${cards}</div>
    ${recLines(a).map((l) => `<p class="cal-rec">➜ ${esc(l)}</p>`).join("")}
    <details class="cal-table"><summary>${TX("Ver todas las fechas", "See all the dates")}</summary>
      <div class="table-wrap"><table class="grid"><thead><tr><th>${TX("Siembra", "Sowing")}</th>${a.cycles.map((c) => `<th>${esc(cyc(c))}<span class="ci">${c.flowering_days} / ${c.maturity_days} ${TX("días a flor / madurez", "days to flowering / maturity")}${c.verify ? " *" : ""}</span></th>`).join("")}</tr></thead><tbody>${trs}</tbody></table></div>
    </details>
    <p class="fine">${TX(`Riesgo = años de cada 10 con helada (Tmin ≤ 0 °C) antes de la madurez fisiológica. ${esc(a.risk_note)}
      La siembra de temporal depende de cuándo lleguen las lluvias: el calendario sirve para escoger ciclo, variedad y fecha dentro de la ventana de humedad.
      ${a.cycles.some((c) => c.verify) ? "* días a madurez por verificar." : ""}`,
      `Risk = years in 10 with frost (Tmin ≤ 0 °C) before physiological maturity. Approximate risk: a normal distribution fitted to the 10th and 50th percentile dates of the first autumn frost.
      Rainfed sowing depends on when the rains arrive: the calendar helps choose the cycle, the variety and the date inside the moisture window.
      ${a.cycles.some((c) => c.verify) ? "* days to maturity still to be verified." : ""}`)}</p>
    <details class="tech"><summary>${TX("Fuentes", "Sources")}</summary><ul class="src">${Object.entries(a.sources).map(([k, v]) => `<li><b>${esc(k)}</b> ${esc(v)}</li>`).join("")}</ul></details>`;
  $("#calParcel").onchange = (e) => select(e.target.value, false);
  $$("#calendar [data-sow]").forEach((el) => (el.onclick = () => { CAL.sow = el.dataset.sow; const open = $(".cal-table").open; renderCalendar(a); $(".cal-table").open = open; }));
  wireTips($(".cal-chart"));
}

async function sendAlerts() {
  const btn = $("#sendBtn");
  btn.disabled = true; btn.textContent = TX("Enviando…", "Sending…");
  try {
    const body = { date: $("#nightDate").value, override_window: $("#overrideWindow").checked, scenario: $("#scenario").value || null, now: demoNow() };
    const r = await api("/api/alerts/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const s = r.summary;
    // the story night: once the evening alerts are out, the clock moves to the next morning, when damage is reported
    if ($("#scenario").value === "demo" && (s.sent || s.ya_avisado) && demoNow() === body.date + "T18:30") {
      const nx = new Date(Date.parse(body.date + "T12:00:00Z") + 864e5).toISOString().slice(0, 10);
      $("#demoNow").value = nx + "T08:00";
    }
    const parts = [];
    const went = (s.sent || 0) + (s.no_seguro || 0) + (s.vigilancia || 0);
    // one alert per parcel per night is the rule: a second click sends nothing, and the reason has to be visible
    const ya = (s.ya_avisado || 0) + (s.vigilancia_ya_avisado || 0);
    const again = !went && ya ? TX("<b>Ya se avisó esta noche: no se manda un segundo aviso a la misma parcela.</b>", "<b>Already alerted tonight: a second alert is never sent to the same plot.</b>") +
      (canReset() ? TX(" Para repetir el recorrido, use «Reiniciar demostración».", " To repeat the walkthrough, use «Reset demo».") : "") : "";
    $("#sendNote").hidden = !again; $("#sendNote").innerHTML = again;      // next to the button, where the click was
    const n = (k, es1, esN, en1, enN) => `${k} ${k === 1 ? TX(es1, en1) : TX(esN, enN)}`;
    if (s.sent) parts.push(`<b>${n(s.sent, "aviso enviado", "avisos enviados", "alert sent", "alerts sent")}</b>`);
    if (r.unsure_reason) parts.push(`<b>${unsureWhy(r.unsure_reason)}${TX(": no se mandó ningún aviso con cifras", ": no alert with numbers was sent")}</b>`);
    if (s.no_seguro) parts.push(n(s.no_seguro, "mensaje «No estoy seguro, pregunte a su técnico» enviado", "mensajes «No estoy seguro, pregunte a su técnico» enviados",
      "«No estoy seguro» (I'm not sure, ask your extension officer) message sent", "«No estoy seguro» (I'm not sure, ask your extension officer) messages sent"));
    const quiet = (s.sin_modelo || 0) + (s.sin_pronostico || 0);
    if (quiet) parts.push(`${quiet} ${TX("sin mensaje (fuera del horario de 18:00 a 20:00)", "with no message (outside 18:00–20:00)")}`);
    if (s.vigilancia) parts.push(n(s.vigilancia, "vigilancia enviada (solo texto)", "vigilancias enviadas (solo texto)", "watch message sent (text only)", "watch messages sent (text only)"));
    const vq = Object.entries(s).filter(([k]) => k.startsWith("vigilancia_") && k !== "vigilancia_ya_avisado").reduce((a, [, v]) => a + v, 0);
    if (vq) parts.push(n(vq, "vigilancia programada o sin enviar", "vigilancias programadas o sin enviar", "watch message scheduled or not sent", "watch messages scheduled or not sent"));
    if (s.fuera_de_ventana) parts.push(`${s.fuera_de_ventana} ${TX("programado(s) para las 18:00", "scheduled for 18:00")}`);
    if (s.tope_semanal) parts.push(`${s.tope_semanal} ${TX("sin enviar por tope semanal (2 por 7 días)", "not sent: weekly cap (2 per 7 days)")}`);
    if (ya) parts.push(`${ya} ${TX("ya avisado(s) esa noche", "already alerted that night")}`);
    if (s.baja) parts.push(`${s.baja} ${TX("dados de baja", "opted out")}`);
    if (s.sin_riesgo) parts.push(`${s.sin_riesgo} ${TX("sin riesgo", "with no risk")}`);
    if (s.failed) parts.push(`${s.failed} ${TX("con error de envío", "failed to send")}`);
    toast(parts.join(" · ") || TX("Sin cambios", "No changes"));
    await loadForecast();
    pollChat();
  } catch (e) { toast(TX("Error al enviar: ", "Could not send: ") + e.message); }
  finally { btn.disabled = false; btn.textContent = TX("Enviar avisos", "Send alerts"); }
}

// Why a night has no validated number (forecast.unsure_reason): nothing with figures goes to a farmer.
const UNSURE_WHY = { sin_modelo: ["El modelo no corrió", "The model did not run"], sin_pronostico: ["No hay pronóstico guardado para esta noche", "There is no forecast saved for this night"] };
const unsureWhy = (why) => TX(...(UNSURE_WHY[why] || UNSURE_WHY.sin_modelo));

function canReset() { return state.cfg && state.cfg.channel === "sim"; }

// Start the demonstration over without the guided ▶ Demo: clears what a run leaves behind (alerts, simulated chats,
// reports, packets, thermometer readings) and puts the clock back at the evening of the night on screen.
async function resetDemo() {
  if (!confirm(TX("¿Reiniciar la demostración?\n\nSe borran los avisos, las conversaciones del teléfono simulado, los reportes y los paquetes. La bitácora se conserva.",
    "Reset the demo?\n\nThis clears the alerts, the simulated phone chats, the reports and the packets. The audit log is kept."))) return;
  const btn = $("#resetBtn");
  btn.disabled = true;
  try {
    await api("/api/demo/reset", { method: "POST" });
    state.lastMsgId = 0; $("#chat").innerHTML = ""; $("#textIn").value = ""; $("#sendNote").hidden = true;
    $("#demoNow").value = "";
    await loadForecast();
    if (!demoNow() && $("#scenario").value === "demo") $("#demoNow").value = state.fc.date + "T18:30";
    pollChat();
    api("/api/packets").then((l) => ($("#pkCount").textContent = l.length)).catch(() => {});
    const tab = $(".tab.active"), reload = tab && TAB_LOAD[tab.dataset.tab];
    if (reload) reload();
    toast(TX("<b>Demostración reiniciada.</b> No hay avisos ni conversaciones: puede empezar con «Enviar avisos».", "<b>Demo reset.</b> There are no alerts or chats: start with «Send alerts»."));
  } catch (e) { toast(TX("No se pudo reiniciar: ", "Could not reset: ") + e.message); }
  finally { btn.disabled = false; }
}

function ruleCell(r) {
  if (state.fc.unsure_reason) return `<span class="rule-watch" title="${unsureWhy(state.fc.unsure_reason)}${TX(": no se manda aviso ni cifras, solo «No estoy seguro, pregunte a su técnico»", ": no alert and no number is sent, only «No estoy seguro» (I'm not sure, ask your extension officer)")}">${TX("No seguro", "Not sure")}</span>`;
  if (r.level === "alert") return `<span class="rule-yes">${TX("Avisar", "Alert")}</span>`;
  if (r.level === "watch") return `<span class="rule-watch" title="${TX("Transferencia por terreno: extremo frío ≤ 0 °C pero P &lt; 30%. Solo texto, sin nota de voz, no cuenta para el tope semanal", "Terrain transfer: the cold end is ≤ 0 °C but P is under 30%. Text only, no voice note, does not count toward the weekly cap")}">${TX("Vigilar", "Watch")}</span>`;
  return '<span class="muted">—</span>';
}

function toast(html) { const t = $("#toast"); t.hidden = false; t.innerHTML = html; }

/* ---------------- packets / audit / backtest ---------------- */
async function loadPackets() {
  const list = await api("/api/packets");
  $("#pkCount").textContent = list.length;
  $("#packets").innerHTML = list.length ? list.map((p) => {
    const s = p.summary;
    // the packet is the farmer's document: its status line is Spanish; the English screen adds what it means
    const status = LANG === "es" ? esc(s.status) : s.complete ? `Documents complete <span class="muted">(«${esc(s.status)}»)</span>` : `Incomplete <span class="muted">(«${esc(s.status)}»)</span>`;
    const cause = LANG === "en" ? ({ helada: "frost", granizo: "hail", sequia: "drought", "sequía": "drought", inundacion: "flood", "inundación": "flood" }[s.cause] || s.cause) : s.cause;
    return `<div class="card"><h4>${esc(p.id)}</h4>
      <div class="muted">${esc(own(s.owner_name))} · ${esc(s.municipality)} · ${TX("Delegación", "District office (Delegación)")} ${esc(s.delegacion)}</div>
      <div class="st ${s.complete ? "ok" : "bad"}">${status}</div>
      <div>${esc(cause || "—")} · ${esc(s.date || "—")} · ${s.area_ha ?? "—"} ha · ${s.photos} ${TX("foto(s)", s.photos === 1 ? "photo" : "photos")}</div>
      ${s.notice_limit ? `<div>${TX("Avisar a la Delegación antes del", "The farmer must notify the district office by")} <b>${esc(s.notice_limit)}</b></div>` : ""}
      ${s.review && s.review.length ? `<ul>${s.review.map((x) => `<li>${TX("Revisar", "To review (as written in the packet)")}: ${esc(x)}</li>`).join("")}</ul>` : ""}
      <a class="btn" href="${p.url}" target="_blank" rel="noopener">${TX("Abrir PDF", "Open the PDF (in Spanish)")}</a>
      <div class="foot">sha256 ${esc(p.sha256.slice(0, 16))}… · ${TX("Lo decide la Secretaría del Campo.", "The state's Secretaría del Campo decides; Helada never does.")}</div></div>`;
  }).join("") : `<p class="muted">${TX("Todavía no hay paquetes. Responda a un aviso desde el teléfono con un audio y fotos.", "No packets yet. Answer an alert from the phone with a voice note and photos.")}</p>`;
}

async function loadAudit() {
  const a = await api("/api/audit?limit=300");
  const v = a.verify;
  $("#verify").className = "verify " + (v.ok ? "ok" : "bad");
  $("#verify").innerHTML = v.ok
    ? TX(`✓ Cadena íntegra: ${v.n} registros · cabeza <span class="mono">${esc(v.head.slice(0, 20))}…</span> · solo se puede agregar (sha256 del registro anterior + registro)`,
         `✓ Chain intact: ${v.n} entries · head <span class="mono">${esc(v.head.slice(0, 20))}…</span> · append-only (sha256 of the previous entry + this entry)`)
    : TX(`✗ Cadena ROTA en el registro #${v.broken_at} (${esc(v.why)})`, `✗ Chain BROKEN at entry #${v.broken_at} (${esc(v.why)})`);
  $("#auditTable tbody").innerHTML = a.entries.map((e) => `<tr><td>${e.seq}</td><td class="mono">${esc(e.ts.replace("+00:00", ""))}</td>
    <td>${esc(e.actor)}</td><td>${esc(e.action)}</td><td class="mono">${esc(e.entity || "")}</td>
    <td class="mono" title="${esc(e.hash)}">${esc(e.hash.slice(0, 12))}…</td></tr>`).join("");
}

/* One real night, per station: what the regional forecast announced, what Helada estimated, what was measured.
   Dot plot on one temperature axis. Colour = series (regional, Helada); the measurement is ink, a diamond. */
function nightChart(fc) {
  const num = (v) => typeof v === "number" && isFinite(v);
  const rows = fc.rows.filter((r) => r.truth && num(r.truth.observed_tmin_c) && num(r.forecast.grid_tmin_c) && num(r.forecast.tmin_c)).map((r) => ({
    id: r.parcel.parcel_id, name: (r.truth.station && r.truth.station.name) || r.parcel.municipality,
    raw: r.forecast.grid_tmin_c, mod: r.forecast.tmin_c, lo: r.forecast.tmin_lo_c, hi: r.forecast.tmin_hi_c,
    obs: r.truth.observed_tmin_c, alert: r.level === "alert",
  })).map((r) => ({ ...r, band: num(r.lo) && num(r.hi) })).sort((a, b) => a.obs - b.obs);   // a row may come without its range
  if (!rows.length) return "";
  const n = rows.length, mae = (k) => rows.reduce((t, r) => t + Math.abs(r[k] - r.obs), 0) / n;
  const frost = rows.filter((r) => r.obs <= 0), caught = frost.filter((r) => r.alert).length;
  const rawFrost = frost.filter((r) => r.raw <= 0).length, falseAl = rows.filter((r) => r.obs > 0 && r.alert).length;
  const all = rows.flatMap((r) => [r.raw, r.mod, r.obs].concat(r.band ? [r.lo, r.hi] : []));
  const rango = (r) => (r.band ? `${fmtT(r.lo)} ${TX("a", "to")} ${fmtT(r.hi)}` : "—");
  const a = Math.floor(Math.min(...all, 0) - 1), b = Math.ceil(Math.max(...all) + 1);
  const W = 760, L = 178, R = 18, T = 30, RH = 30, H = T + n * RH + 34;
  const x = (v) => L + ((v - a) / (b - a)) * (W - L - R);
  let g = "";
  for (let v = Math.ceil(a / 2) * 2; v <= b; v += 2) {
    g += `<line class="nc-grid${v === 0 ? " zero" : ""}" x1="${x(v)}" y1="${T - 6}" x2="${x(v)}" y2="${T + n * RH}"/>` +
         `<text class="nc-tick" x="${x(v)}" y="${T + n * RH + 16}">${v === 0 ? "0 °C" : (v < 0 ? "−" + -v : v)}</text>`;
  }
  rows.forEach((r, i) => {
    const y = T + i * RH + RH / 2;
    g += `<text class="nc-name" x="${L - 12}" y="${y + 4}">${esc(r.name)}</text>` +
         (r.band ? `<line class="nc-range" x1="${x(r.lo)}" y1="${y}" x2="${x(r.hi)}" y2="${y}"/>` : "") +
         `<circle class="nc-raw" cx="${x(r.raw)}" cy="${y}" r="5.5"/>` +
         `<circle class="nc-mod" cx="${x(r.mod)}" cy="${y}" r="5.5"/>` +
         `<rect class="nc-obs" x="${x(r.obs) - 5}" y="${y - 5}" width="10" height="10" transform="rotate(45 ${x(r.obs)} ${y})"/>` +
         `<rect class="nc-hit" x="0" y="${y - RH / 2}" width="${W}" height="${RH}" data-tip="${esc(r.name)} (${r.id}): ${TX("pronóstico regional", "regional forecast")} ${fmtT(r.raw)} · Helada ${fmtT(r.mod)}${r.band ? ` (${TX("entre", "between")} ${fmtT(r.lo)} ${TX("y", "and")} ${fmtT(r.hi)})` : ""} · ${TX("se midió", "measured")} ${fmtT(r.obs)}"/>`;
  });
  const table = rows.map((r) => `<tr><td>${esc(r.name)} (${r.id})</td><td class="num">${fmtT(r.raw)}</td><td class="num">${fmtT(r.mod)}</td><td class="num">${rango(r)}</td><td class="num">${fmtT(r.obs)}</td><td>${r.alert ? TX("sí", "yes") : "no"}</td></tr>`).join("");
  return `<section class="nc">
    <h3>${TX("Una noche real: 14 al 15 de noviembre de 2025", "One real night: 14–15 November 2025")}</h3>
    <p class="nc-lead">${TX(`Heló en <b>${frost.length} de ${n}</b> ${n === 1 ? "estación" : "estaciones"}. El pronóstico regional anunciaba helada en <b>${rawFrost}</b>; Helada avisó en <b>${caught}</b>${falseAl ? `, con ${falseAl} ${falseAl === 1 ? "falsa alarma" : "falsas alarmas"}` : ""}.
      Error promedio esa noche: pronóstico regional <b>${mae("raw").toFixed(1)} °C</b>, Helada <b>${mae("mod").toFixed(1)} °C</b>.`,
      `There was frost at <b>${frost.length} of ${n}</b> ${n === 1 ? "station" : "stations"}. The regional forecast announced frost at <b>${rawFrost}</b>; Helada alerted at <b>${caught}</b>${falseAl ? `, with ${falseAl} false ${falseAl === 1 ? "alarm" : "alarms"}` : ""}.
      Average error that night: regional forecast <b>${mae("raw").toFixed(1)} °C</b>, Helada <b>${mae("mod").toFixed(1)} °C</b>.`)}</p>
    <div class="nc-legend"><span><i class="k raw"></i>${TX("Pronóstico regional", "Regional forecast")}</span><span><i class="k mod"></i>${TX("Helada (la línea es su rango probable)", "Helada (the line is its likely range)")}</span><span><i class="k obs"></i>${TX("Lo que se midió", "What was measured")}</span></div>
    <div class="nc-plot"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${TX("Por estación: pronóstico regional, cálculo de Helada y temperatura medida la noche del 14 al 15 de noviembre de 2025", "By station: regional forecast, Helada's estimate and the temperature measured on the night of 14–15 November 2025")}">${g}</svg><div class="nc-tip" hidden></div></div>
    <details><summary>${TX("Ver los números", "See the numbers")}</summary><div class="table-wrap"><table class="grid"><thead><tr><th>${TX("Estación (parcela)", "Station (plot)")}</th><th class="num">${TX("Pronóstico regional", "Regional forecast")}</th><th class="num">Helada</th><th class="num">${TX("Rango probable", "Likely range")}</th><th class="num">${TX("Se midió", "Measured")}</th><th>${TX("Avisó", "Alerted")}</th></tr></thead><tbody>${table}</tbody></table></div></details>
    <p class="fine">${TX(`Una sola noche, ${n} ${n === 1 ? "estación" : "estaciones"}: es un ejemplo. El modelo de esta noche se entrenó sin la temporada 2025-26. La evidencia completa está abajo.`,
      `One night, ${n} ${n === 1 ? "station" : "stations"}: it is an example. The model shown for this night was trained without the 2025-26 season. The full evidence is below.`)}</p>
  </section>`;
}
function wireTips(plot) {
  if (!plot) return;
  const tip = plot.querySelector(".nc-tip");
  plot.querySelectorAll(".nc-hit").forEach((el) => {
    el.addEventListener("mousemove", (e) => {
      const box = plot.getBoundingClientRect();
      tip.textContent = el.dataset.tip; tip.hidden = false;
      tip.style.left = Math.max(0, Math.min(e.clientX - box.left + 12, box.width - 280)) + "px"; tip.style.top = (e.clientY - box.top + 14) + "px";
    });
    el.addEventListener("mouseleave", () => { tip.hidden = true; });
  });
}

async function loadBacktest() {
  const b = await api("/api/backtest");
  let h = "";
  try { h += nightChart(await api("/api/forecast?scenario=demo")); } catch (e) { /* the chart is optional */ }
  if (b.source === "TEMP_STUB") {
    h += `<div class="banner">${esc(b.note || "")}</div>`;
  } else {
    h += evidencePanel(b);
  }
  $("#backtest").innerHTML = h || `<p class="muted">${TX("Sin datos.", "No data.")}</p>`;
  wireTips($(".nc .nc-plot"));
}


/* ---------------- evidence panel (helada_model.backtest_report) ---------------- */
function evidencePanel(b) {
  const REG = [
    { key: "known_station", name: TX("Cerca de estación SMN", "Near an SMN station"), low: TX("cerca de estación SMN", "near an SMN station"), note: TX("temporada 2025-26, no vista en el entrenamiento", "2025-26 season, not seen in training") },
    { key: "unseen_site", name: TX("Sin estación cercana", "No station nearby"), low: TX("sin estación cercana", "no station nearby"), note: TX("estaciones retenidas (10 grupos), 2024-25 y 2025-26", "held-out stations (10 groups), 2024-25 and 2025-26") },
  ];
  const num = (x, d = 1) => Number(x).toFixed(d).replace("-", "−");
  const p0 = (x) => `${Math.round(x * 100)}%`;
  const pp = (x) => `${x >= 0 ? "+" : "−"}${Math.abs(Math.round(x * 100))}`;
  const rows = REG.map((r) => {
    const k = b[r.key] || {}, v = k.vs_open_meteo_default || {};
    const raw = v.raw_best_match || {}, mod = v.model || {};
    return { ...r, k, raw, mod, dmae: v.delta_mae_ci, drec: v.delta_recall_pofd5_ci };
  }).filter((r) => r.k.n);
  if (!rows.length) return `<p class="muted">${TX("Sin datos.", "No data.")}</p>`;

  const ci = (a, f) => (a ? `<span class="ci">${TX("IC 95%", "95% CI")}: ${f(a[0])} ${TX("a", "to")} ${f(a[1])}</span>` : "");
  const tbl = rows.map((r) => `<tr>
      <th scope="row">${esc(r.name)}<span class="ci">${esc(r.note)}</span></th>
      <td class="num">${r.k.stations}</td>
      <td class="num">${r.k.n_frost.toLocaleString(LOCALE())}<span class="ci">${TX("de", "of")} ${r.k.n.toLocaleString(LOCALE())} ${TX("noches-estación", "station-nights")}</span></td>
      <td class="num"><b>${num(r.raw.mae, 2)} → ${num(r.mod.mae, 2)} °C</b>${ci(r.dmae, (x) => "−" + num(x, 2) + " °C")}</td>
      <td class="num"><b>${p0(r.raw.recall_at_pofd5)} → ${p0(r.mod.recall_at_pofd5)}</b>${ci(r.drec, (x) => pp(x) + " pp")}</td>
    </tr>`).join("");

  // grouped bars, recall at 5% false alarms, 0-100% (to scale)
  const W = 560, H = 250, L = 44, T = 22, B = 58, ph = H - T - B, pw = W - L - 12;
  const y = (v) => T + ph * (1 - v);
  const grid = [0, .25, .5, .75, 1].map((g) => `<line class="gl" x1="${L}" x2="${W - 12}" y1="${y(g)}" y2="${y(g)}"/><text class="ax" x="${L - 6}" y="${y(g) + 4}" text-anchor="end">${Math.round(g * 100)}%</text>`).join("");
  const gw = pw / rows.length, bw = Math.min(64, gw / 3);
  const bars = rows.map((r, i) => {
    const cx = L + gw * (i + .5);
    const one = (x, val, cls) => {
      const h = ph * val, top = y(val);
      return `<path class="${cls}" d="M${x} ${T + ph} V${top + 4} q0 -4 4 -4 h${bw - 8} q4 0 4 4 V${T + ph} Z"><title>${esc(r.name)}: ${p0(val)}</title></path>
        <text class="val" x="${x + bw / 2}" y="${top - 5}" text-anchor="middle">${p0(val)}</text>`;
    };
    return one(cx - bw - 1, r.raw.recall_at_pofd5, "b-raw") + one(cx + 1, r.mod.recall_at_pofd5, "b-mod")
      + `<text class="cat" x="${cx}" y="${T + ph + 20}" text-anchor="middle">${esc(r.name)}</text>`;
  }).join("");
  const svg = `<svg class="evchart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${TX("Heladas detectadas al 5% de falsas alarmas: pronóstico crudo frente a Helada, por régimen", "Frosts detected at 5% false alarms: raw forecast against Helada, by setting")}">
      ${grid}<line class="axl" x1="${L}" x2="${W - 12}" y1="${T + ph}" y2="${T + ph}"/>${bars}
      <g transform="translate(${L},${H - 16})"><rect class="b-raw" width="12" height="12" rx="2"/><text class="ax" x="18" y="10">${TX("Pronóstico crudo (Open-Meteo)", "Raw forecast (Open-Meteo)")}</text>
      <rect class="b-mod" x="215" width="12" height="12" rx="2"/><text class="ax" x="233" y="10">Helada</text></g></svg>`;

  // reliability, both regimes side by side
  const rel = rows.map((r) => (r.k.calibration || {}).reliability || []);
  const nb = Math.max(...rel.map((x) => x.length));
  let rrows = "";
  for (let i = 0; i < nb; i++) {
    const cells = rel.map((x) => {
      const q = x[i];
      return q ? `<td class="num">${p0(q.p_mean)}</td><td class="num">${p0(q.obs_freq)}</td><td class="num n">${q.n.toLocaleString(LOCALE())}</td>` : `<td></td><td></td><td></td>`;
    }).join("");
    const bin = rel[0][i] || rel[1][i];
    rrows += `<tr><th scope="row">${Math.round(bin.bin[0] * 100)}–${Math.round(bin.bin[1] * 100)}%</th>${cells}</tr>`;
  }
  const relHead = `<tr><th rowspan="2">${TX("Probabilidad", "Probability")}</th>${rows.map((r) => `<th colspan="3" class="grp">${esc(r.name)}</th>`).join("")}</tr>
    <tr>${rows.map(() => `<th class="num">${TX("predicho", "predicted")}</th><th class="num">${TX("observado", "observed")}</th><th class="num">n</th>`).join("")}</tr>`;
  const brier = rows.map((r) => `${esc(r.low)}: ${num(r.k.calibration.brier_model, 3)} (${TX("climatología", "climatology")} ${num(r.k.calibration.brier_climatology, 3)})`).join("; ");

  return `<div class="evid">
    <h3>${TX("Evidencia del modelo: pronóstico crudo frente a Helada", "Model evidence: the raw forecast against Helada")}</h3>
    <p class="muted lead">${TX("Verdad: Tmin de las estaciones SMN (lectura de las 08:00). Helada = Tmin ≤ 0 °C. «Crudo» es el pronóstico de Open-Meteo (ecmwf_ifs025, día 1) sin corregir.",
      "Ground truth: Tmin at the national weather service (SMN) stations (08:00 reading). Frost = Tmin ≤ 0 °C. «Raw» is the Open-Meteo forecast (ecmwf_ifs025, day 1), uncorrected.")}</p>
    <div class="table-wrap"><table class="grid evtable">
      <thead><tr><th>${TX("Régimen", "Setting")}</th><th class="num">${TX("Estaciones", "Stations")}</th><th class="num">${TX("Noches con helada", "Frost nights")}</th><th class="num">${TX("Error Tmin (MAE)", "Tmin error (MAE)")}<span class="ci">${TX("crudo → Helada", "raw → Helada")}</span></th><th class="num">${TX("Heladas detectadas al 5% de falsas alarmas", "Frosts detected at 5% false alarms")}<span class="ci">${TX("crudo → Helada", "raw → Helada")}</span></th></tr></thead>
      <tbody>${tbl}</tbody></table></div>
    <div class="evgrid">
      <figure class="evfig"><figcaption>${TX("Heladas detectadas al 5% de falsas alarmas", "Frosts detected at 5% false alarms")}</figcaption>${svg}</figure>
      <div class="evrel"><h4>${TX("Confiabilidad de la probabilidad", "Reliability of the probability")}</h4>
        <div class="table-wrap"><table class="grid rel"><thead>${relHead}</thead><tbody>${rrows}</tbody></table></div>
        <p class="fine">${TX("Predicho = probabilidad media que dio el modelo; observado = fracción de noches que sí helaron.", "Predicted = the mean probability the model gave; observed = the share of nights that did freeze.")} Brier ${brier}.</p></div>
    </div>
    <p class="fine evnote">${TX("<b>Lo que sí y lo que no.</b> Cerca de una estación el modelo aporta habilidad real: menos error y más heladas detectadas. En otros lugares, sobre todo quita el sesgo cálido del pronóstico y lo dice con una banda más ancha, sin detectar más heladas de forma clara (el intervalo de confianza incluye cero). Por eso la precondición es instalar registradores de temperatura en las parcelas.",
      "<b>What it does and what it does not.</b> Near a station the model adds real skill: less error and more frosts detected. Elsewhere it mostly removes the forecast's warm bias and says so with a wider band, without clearly detecting more frosts (the confidence interval includes zero). That is why the precondition is to put temperature loggers on the plots.")}</p>
    <p class="fine evnote">${TX("<b>Réplica del 14→15 nov 2025.</b> Usa el modelo entrenado sin la temporada 2025-26 (variante holdout_2025_26), así la demostración nunca muestra un resultado visto en el entrenamiento.",
      "<b>Replay of 14–15 Nov 2025.</b> It uses the model trained without the 2025-26 season (variant holdout_2025_26), so the demo never shows a result seen in training.")}</p>
    <details class="rawjson"><summary>${TX("Ver el informe completo (JSON)", "See the full report (JSON)")}</summary><pre class="mono">${esc(JSON.stringify(b, null, 2))}</pre></details>
  </div>`;
}

/* ---------------- phone simulator ---------------- */
async function initPhone() {
  const r = await api("/api/parcels");
  state.parcels = r.parcels;
  renderPhoneList();
  $("#phoneSel").onchange = () => {
    const ph = $("#phoneSel").value, p = state.parcels.find((x) => x.phone === ph);
    setPhone(ph);
    if (p && p.parcel_id !== state.sel && state.fc) select(p.parcel_id);
  };
  // the parcel on screen may already be chosen (the forecast loads at the same time)
  state.phone = null;
  setPhone((r.parcels.find((p) => p.parcel_id === state.sel) || r.parcels[0])?.phone);
  $("#composer").onsubmit = async (e) => {
    e.preventDefault();
    const t = $("#textIn").value.trim();
    if (!t) return;
    $("#textIn").value = "";
    await inbound({ text: t });
  };
  $("#fileIn").onchange = async () => { const f = $("#fileIn").files[0]; if (f) await inbound({ file: f }); $("#fileIn").value = ""; };
  $("#sampleAudio").onclick = () => sample("audio");
  $("#samplePhoto").onclick = () => sample("photo");
  $("#sampleLoc").onclick = () => sample("location");
  initMic();
  pollChat();
  setInterval(pollChat, 2500);
}

function renderPhoneList() {
  const sel = $("#phoneSel"), cur = sel.value;
  sel.innerHTML = state.parcels.map((p) => `<option value="${esc(p.phone)}">${esc(own(p.owner_name))} · ${p.parcel_id} · ${esc(p.municipality)}</option>`).join("");
  if (cur) sel.value = cur;
}

/** The simulated phone talks as one parcel: the one selected on the map, the table or the calendar. */
function setPhone(phone) {
  const sel = $("#phoneSel");
  if (!phone || ![...sel.options].some((o) => o.value === phone)) return;   // before the list of phones has loaded
  if (state.phone === phone && sel.value === phone) return;
  state.phone = phone; sel.value = phone;
  state.lastMsgId = 0; $("#chat").innerHTML = "";
  pollChat();
}

function typing(on) {
  let t = $("#chat .typing");
  if (on && !t) { t = document.createElement("div"); t.className = "typing"; t.textContent = TX("Helada está escribiendo…", "Helada is typing…"); $("#chat").appendChild(t); scrollChat(); }
  if (!on && t) t.remove();
}

async function inbound({ text, file }) {
  const fd = new FormData();
  fd.set("phone", state.phone);
  if (text) fd.set("text", text);
  if (file) fd.set("file", file, file.name || "voz.webm");
  if (demoNow()) fd.set("now", demoNow());
  typing(true);
  try { await api("/api/sim/inbound", { method: "POST", body: fd }); }
  catch (e) { alert("Error: " + e.message); }
  finally { typing(false); await pollChat(); refreshSide(); }
}

async function sample(what) {
  const fd = new FormData();
  fd.set("phone", state.phone); fd.set("what", what);
  if (demoNow()) fd.set("now", demoNow());
  typing(true);
  try { await api("/api/sim/sample", { method: "POST", body: fd }); }
  catch (e) { alert("Error: " + e.message); }
  finally { typing(false); await pollChat(); refreshSide(); }
}

function refreshSide() {
  const active = $(".tab.active").dataset.tab;
  if (active === "paquetes") loadPackets(); else api("/api/packets").then((l) => ($("#pkCount").textContent = l.length));
  if (active === "bitacora") loadAudit();
}

let polling = false;
async function pollChat() {
  if (!state.phone || polling) return;
  polling = true;
  const phone = state.phone;
  try {
    const msgs = await api(`/api/sim/messages?phone=${encodeURIComponent(phone)}&since_id=${state.lastMsgId}`);
    if (phone !== state.phone) return;   // the phone changed while this was in flight: these are the other chat's
    let first = null;   // start of the first new message from Helada (a long alert is read from its top)
    for (const m of msgs) { const el = renderMsg(m); if (!first && m.direction !== "in") first = el; state.lastMsgId = Math.max(state.lastMsgId, m.id); }
    if (msgs.length) scrollChat(first);
  } catch (e) { /* offline */ }
  finally { polling = false; if (phone !== state.phone) pollChat(); }
}
function scrollChat(el) {
  const c = $("#chat");
  if (el) c.scrollTop = c.scrollTop + el.getBoundingClientRect().top - c.getBoundingClientRect().top - 8;
  else c.scrollTop = c.scrollHeight;
}

function renderMsg(m) {
  const mine = m.direction === "in";
  const el = document.createElement("div");
  el.className = "bub " + (mine ? "from-me" : "from-helada");
  const time = new Date(m.created_at).toLocaleTimeString(LOCALE(), { hour: "2-digit", minute: "2-digit" });
  let body = "";
  if (!mine && m.meta && m.meta.alert_id && m.kind === "text") body += `<div class="sender">${TX("Helada · aviso", "Helada · alert")}</div>`;
  if (m.kind === "text") body += esc(m.text);
  else if (m.kind === "audio") {
    body += m.media_url ? `<audio controls preload="none" src="${m.media_url}"></audio>` : TX("🎤 nota de voz", "🎤 voice note");
    if (m.meta && m.meta.transcript) body += `<div class="tr">“${esc(m.meta.transcript)}” <span class="muted">(${esc(m.meta.asr)})</span></div>`;
  } else if (m.kind === "image") body += m.media_url ? `<img src="${m.media_url}" alt="${TX("foto enviada", "photo sent")}">` : TX("📷 foto", "📷 photo");
  else if (m.kind === "document") body += `<a class="doc" href="${m.media_url}" target="_blank" rel="noopener"><span class="ic">PDF</span><span>${esc(m.text || "paquete.pdf")}</span></a>`;
  else if (m.kind === "location") body += `📍 ${TX("Ubicación compartida", "Location shared")}<br><span class="tr">${m.meta?.location ? m.meta.location.lat.toFixed(5) + ", " + m.meta.location.lon.toFixed(5) : ""}</span>`;
  el.innerHTML = body + `<span class="time">${time}</span>`;
  const t = $("#chat .typing");
  $("#chat").insertBefore(el, t || null);
  return el;
}

/* voice recording (MediaRecorder -> webm/opus or mp4; server converts with ffmpeg) */
function initMic() {
  const btn = $("#micBtn");
  let rec = null, chunks = [];
  if (!navigator.mediaDevices || !window.MediaRecorder) { btn.disabled = true; btn.title = TX("Grabación no disponible en este navegador", "Recording is not available in this browser"); return; }
  btn.onclick = async () => {
    if (rec && rec.state === "recording") { rec.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : (MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "");
      rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
      chunks = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        btn.classList.remove("rec");
        const type = rec.mimeType || "audio/webm";
        const blob = new Blob(chunks, { type });
        const f = new File([blob], type.includes("mp4") ? "voz.m4a" : "voz.webm", { type: type.split(";")[0] });
        await inbound({ file: f });
      };
      rec.start();
      btn.classList.add("rec");
    } catch (e) { alert(TX("No se pudo usar el micrófono: ", "Could not use the microphone: ") + e.message); }
  };
}

/* ---------------- «Registrador en la parcela» ---------------- */
async function loadLogger() {
  const sel = $("#lgParcel");
  if (!sel.options.length) {
    const r = await api("/api/parcels");
    sel.innerHTML = r.parcels.map((p) => `<option value="${esc(p.parcel_id)}">${esc(p.parcel_id)} · ${esc(own(p.owner_name))} · ${esc(p.municipality)}${p.near_station ? "" : TX(" (sin estación cercana)", " (no station nearby)")}</option>`).join("");
    const firstTransfer = r.parcels.find((p) => !p.near_station);
    if (firstTransfer) sel.value = firstTransfer.parcel_id;
    sel.onchange = showLoggerParcel;
    $("#lgForm").onsubmit = saveLogger;
    $("#lgDemo").onclick = showLoggerDemo;
  }
  (LG.last || showLoggerParcel)();
}
function lgContext() {
  const q = new URLSearchParams();
  if ($("#scenario").value) q.set("scenario", $("#scenario").value); else if ($("#nightDate").value) q.set("date", $("#nightDate").value);
  return q;
}
const LG = { last: null };     // what the result panel shows, to draw it again when the language changes
async function showLoggerParcel() {
  LG.last = showLoggerParcel;
  const pid = $("#lgParcel").value;
  $("#lgResult").innerHTML = `<p class="muted">${TX("Cargando…", "Loading…")}</p>`;
  try {
    const r = await api(`/api/logger/${encodeURIComponent(pid)}?${lgContext()}`);
    renderLogger({ title: pid, k: r.k, n: r.n_readings, precision: r.precision, before: r.compare?.before, after: r.compare?.after,
      date: r.compare?.date, curve: r.curve, skipped: r.skipped, readings: r.readings, label: r.compare?.model_variant_label });
  } catch (e) { $("#lgResult").innerHTML = `<p class="bad">Error: ${esc(e.message)}</p>`; }
}
async function saveLogger(ev) {
  ev.preventDefault();
  const pid = $("#lgParcel").value, fd = new FormData(), file = $("#lgFile").files[0];
  if (file) fd.set("file", file); else if ($("#lgText").value.trim()) fd.set("text", $("#lgText").value);
  else { alert(TX("Pegue lecturas o elija un CSV.", "Paste readings or choose a CSV.")); return; }
  fd.set("date_is", $("input[name=lgDateIs]:checked").value);
  lgContext().forEach((v, k) => fd.set(k, v));
  $("#lgSave").disabled = true;
  try {
    const res = await fetchRetry(`/api/logger/${encodeURIComponent(pid)}/readings`, { method: "POST", body: fd });
    const r = await res.json();
    if (!res.ok && !r.errors) throw new Error(JSON.stringify(r));
    const c = r.calibration || {};
    renderLogger({ title: pid, k: c.k || 0, n: c.n_readings || 0, precision: c.precision, before: r.compare?.before, after: r.compare?.after,
      date: r.compare?.date, curve: r.curve, skipped: c.skipped, errors: r.errors, added: r.added, label: r.compare?.model_variant_label });
    $("#lgText").value = ""; $("#lgFile").value = "";
    refreshSide();
  } catch (e) { alert("Error: " + e.message); }
  finally { $("#lgSave").disabled = false; }
}
async function showLoggerDemo() {
  $("#lgResult").innerHTML = `<p class="muted">${TX("Cargando…", "Loading…")}</p>`;
  const r = await api("/api/logger/demo");
  const sc = r.season_check?.cut;
  LG.last = showLoggerDemo;
  renderLogger({ title: TX(r.site.label, r.site.label.replace("Registrador de ejemplo en", "Example logger at")), k: r.k, n: r.k, precision: r.precision, before: r.before, after: r.after, date: r.until,
    observed: r.observed_tmin_c, curve: r.curve,
    note: TX(r.note, "Example with real data: the daily minimums of SMN station 15390 (E.T.A. 013 Jocotitlán), which the model never saw, stand in for a logger. One station and one season: it illustrates, it does not prove. The evidence is the curve measured at 80 stations (model/README.md)."),
    check: sc ? TX(`Resto de la temporada 2025-26 en esa estación (${sc.nights} noches después del ${sc.first_night}, calibración fija con ${sc.k} noches): error medio ${sc.mae_before_c.toFixed(2)} → <b>${sc.mae_after_c.toFixed(2)} °C</b>; rango 80% contuvo el valor real en ${pct(sc.cov80_before)} → ${pct(sc.cov80_after)} de las noches.`,
      `The rest of the 2025-26 season at that station (${sc.nights} nights after ${sc.first_night}, calibration fixed at ${sc.k} nights): mean error ${sc.mae_before_c.toFixed(2)} → <b>${sc.mae_after_c.toFixed(2)} °C</b>; the 80% range held the real value on ${pct(sc.cov80_before)} → ${pct(sc.cov80_after)} of the nights.`) : null,
    csv: r.csv_example, label: TX("Réplica: modelo entrenado sin la temporada 2025-26", "Replay: model trained without the 2025-26 season") });
}
function bandSvg(rows, observed) {
  const vals = rows.flatMap((r) => [r.b.tmin_lo_c, r.b.tmin_hi_c]).concat(observed == null ? [] : [observed], [0]);
  const lo = Math.floor(Math.min(...vals) - 1), hi = Math.ceil(Math.max(...vals) + 1);
  const W = 560, L = 150, R = 20, H = 34 * rows.length + 40, x = (t) => L + (t - lo) / (hi - lo) * (W - L - R);
  let g = "";
  for (let t = lo; t <= hi; t++) if ((t - lo) % Math.max(1, Math.round((hi - lo) / 8)) === 0)
    g += `<line x1="${x(t)}" x2="${x(t)}" y1="8" y2="${H - 22}" class="lg-tick"/><text x="${x(t)}" y="${H - 6}" class="lg-ax">${t}</text>`;
  g += `<line x1="${x(0)}" x2="${x(0)}" y1="4" y2="${H - 22}" class="lg-zero"/>`;
  rows.forEach((r, i) => {
    const y = 22 + i * 34;
    g += `<text x="${L - 10}" y="${y + 5}" class="lg-lab">${esc(r.label)}</text>`;
    g += `<rect x="${x(r.b.tmin_lo_c)}" y="${y - 7}" width="${Math.max(2, x(r.b.tmin_hi_c) - x(r.b.tmin_lo_c))}" height="14" rx="7" class="lg-bar ${r.cls}"/>`;
    g += `<circle cx="${x(r.b.tmin_c)}" cy="${y}" r="5" class="lg-dot"/>`;
  });
  if (observed != null) g += `<path d="M${x(observed)} ${10} l6 6 l-6 6 l-6 -6z" class="lg-obs"/><line x1="${x(observed)}" x2="${x(observed)}" y1="16" y2="${H - 22}" class="lg-obsl"/>`;
  return `<svg viewBox="0 0 ${W} ${H}" class="lg-svg" role="img" aria-label="${TX("Rango de temperatura mínima antes y después", "Range of the minimum temperature, before and after")}">${g}</svg>`;
}
function renderLogger(o) {
  const p = o.precision || {}, before = (o.curve || [])[0] || {};
  let h = `<h3>${esc(o.title)}</h3>`;
  if (o.note) h += `<p class="fine">${esc(o.note)}</p>`;
  if (o.added != null) h += `<p class="ok">${TX(`Se guardaron ${o.added} lecturas.`, `${o.added} readings saved.`)}</p>`;
  h += `<div class="lg-kpis"><div><span class="k">${o.k}</span><span class="l">${TX("noches registradas", "nights recorded")}${o.n > o.k ? TX(` (de ${o.n} lecturas)`, ` (from ${o.n} readings)`) : ""}</span></div>`
    + `<div><span class="k">±${(p.mae_c ?? before.mae_c ?? 0).toFixed(1)} °C</span><span class="l">${TX("precisión esperada (error típico)", "expected precision (typical error)")}<br>${TX("sin registrador", "without a logger")}: ±${(before.mae_c ?? 0).toFixed(1)} °C</span></div></div>`;
  if (o.before && o.after) {
    const rows = [{ label: TX("Sin registrador", "Without a logger"), b: o.before, cls: "b0" }, { label: TX("Con registrador", "With a logger"), b: o.after, cls: "b1" }];
    const lab = LANG === "en" && o.label ? o.label.replace("Réplica: modelo entrenado sin la temporada 2025-26", "Replay: model trained without the 2025-26 season") : o.label;
    h += `<p class="fine">${TX("Noche del", "Night of")} ${esc(o.date)}${lab ? " · " + esc(lab) : ""}. ${TX("Barra = rango 80%, punto = cálculo", "Bar = 80% range, dot = estimate")}${o.observed != null ? TX(", rombo = medido", ", diamond = measured") : ""}.</p>` + bandSvg(rows, o.observed);
    h += `<table class="grid lg-t"><thead><tr><th></th><th class="num">${TX("Su parcela", "On the plot")}</th><th class="num">${TX("Rango 80%", "80% range")}</th><th class="num">${TX("P(helada)", "P(frost)")}</th><th>${TX("Soporte", "Basis")}</th></tr></thead><tbody>`
      + rows.map((r) => `<tr><td>${r.label}</td><td class="num">${fmtT(r.b.tmin_c)}</td><td class="num">${fmtT(r.b.tmin_lo_c)} ${TX("a", "to")} ${fmtT(r.b.tmin_hi_c)}</td><td class="num">${pct(r.b.p_frost)}</td><td>${supportTag(r.b)}</td></tr>`).join("")
      + (o.observed != null ? `<tr><td>${TX("Medido", "Measured")}</td><td class="num">${fmtT(o.observed)}</td><td></td><td></td><td></td></tr>` : "") + `</tbody></table>`;
  } else if (!o.n) h += `<p class="muted">${TX("Esta parcela no tiene lecturas todavía.", "This plot has no readings yet.")}</p>`;
  if (o.check) h += `<p class="lg-check">${o.check}</p>`;
  if (o.curve) h += `<details class="lg-curve" open><summary>${TX("Curva medida: noches de registrador → error típico", "Measured curve: nights of logger data → typical error")}</summary><table class="grid"><thead><tr>${o.curve.map((c) => `<th class="num${c.level_k === p.level_k ? " cur" : ""}">${c.k === 182 ? TX("1 temporada", "1 season") : c.k}</th>`).join("")}</tr></thead><tbody><tr>${o.curve.map((c) => `<td class="num${c.level_k === p.level_k ? " cur" : ""}">±${c.mae_c.toFixed(2)}</td>`).join("")}</tr></tbody></table><p class="fine">${TX(`Error medio absoluto de la mínima en 80 estaciones SMN que el modelo nunca vio (noches de ene–mar); estación conocida: ±${(p.ceiling_mae_c ?? 1.55).toFixed(2)} °C. Detectar heladas mejoró de forma confiable solo con una temporada completa: con menos noches se mantiene el aviso de «vigilar».`,
    `Mean absolute error of the minimum at 80 SMN stations the model never saw (January–March nights); a known station: ±${(p.ceiling_mae_c ?? 1.55).toFixed(2)} °C. Frost detection improved reliably only with a full season: with fewer nights the «watch» message stays.`)}</p></details>`;
  const bad = [...(o.errors || []).map((e) => `${TX("renglón", "line")} ${e.line}: ${esc(e.text)} (${esc(e.reason)})`), ...(o.skipped || []).map((e) => `${esc(e.date)}: ${esc(e.reason)}`)];
  if (bad.length) h += `<details class="lg-bad"><summary>${bad.length} ${TX("lecturas no usadas", "readings not used")}</summary><ul>${bad.slice(0, 40).map((b) => `<li>${b}</li>`).join("")}</ul></details>`;
  if (o.csv) h += `<details><summary>${TX(`CSV del ejemplo (${o.k} noches)`, `The example's CSV (${o.k} nights)`)}</summary><pre class="lg-csv">${esc(o.csv.split("\n").slice(0, 12).join("\n"))}\n…</pre></details>`;
  $("#lgResult").innerHTML = h;
}

/* ---------------- language ---------------- */
/** Switch the screen language in place: static texts, then everything this file draws. The chat is drawn again
    too (its labels change; the messages themselves stay as the farmer got them, in Spanish). */
function setLang(lang) {
  if (lang !== "es" && lang !== "en" || lang === LANG) return;
  LANG = lang;
  try { localStorage.setItem("helada-lang", lang); } catch (e) { /* ignore */ }
  applyStatic();
  $("#sendNote").hidden = true; $("#toast").hidden = true;
  if (state.cfg) { renderChips(); renderAsrHint(); }
  renderLegend();
  if (state.parcels.length) renderPhoneList();
  $("#lgParcel").innerHTML = "";                      // the logger list is built again, in the new language, when its tab loads
  if (state.fc) renderForecast();
  const tab = $(".tab.active"), reload = tab && TAB_LOAD[tab.dataset.tab];
  if (reload) reload();
  state.lastMsgId = 0; $("#chat").innerHTML = ""; pollChat();
  document.dispatchEvent(new CustomEvent("helada:lang", { detail: lang }));
}

/* ---------------- boot ---------------- */
(async function boot() {
  applyStatic();
  $("#langBtn").onclick = () => setLang(LANG === "es" ? "en" : "es");
  initTheme(); initTabs(); initMap();
  const devBtn = $("#devBtn");
  const setDev = (on) => { document.body.classList.toggle("dev", on); devBtn.setAttribute("aria-pressed", String(on)); try { localStorage.setItem("helada-dev", on ? "1" : ""); } catch (e) { /* ignore */ } };
  let devOn = false; try { devOn = localStorage.getItem("helada-dev") === "1"; } catch (e) { /* ignore */ }
  setDev(devOn);
  devBtn.onclick = () => setDev(!document.body.classList.contains("dev"));
  const today = new Date();
  const iso = new Date(today.getTime() - today.getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
  $("#nightDate").value = iso;
  $("#refreshBtn").onclick = loadForecast;
  // «Esta noche, en vivo» means tonight: leave the replay's date and clock behind (the replay sets its own again)
  $("#scenario").onchange = () => { if (!$("#scenario").value) { $("#nightDate").value = iso; $("#demoNow").value = ""; $("#sendNote").hidden = true; } loadForecast(); };
  $("#nightDate").onchange = loadForecast;
  $("#sendBtn").onclick = sendAlerts;
  $("#resetBtn").onclick = resetDemo;
  await loadConfig();
  $("#resetBtn").hidden = !canReset();
  await Promise.all([loadForecast(), initPhone()]);
  api("/api/packets").then((l) => ($("#pkCount").textContent = l.length)).catch(() => {});
})();
