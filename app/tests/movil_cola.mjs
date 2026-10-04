// The rules of the phone's send queue (static/movil/cola.js): what each tab counts and what a row says.
//   node tests/movil_cola.mjs        exit 0 = every check passed
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const Q = createRequire(import.meta.url)(join(here, "..", "static", "movil", "cola.js"));

let bad = 0, n = 0;
const eq = (got, want, msg) => { n += 1; if (JSON.stringify(got) !== JSON.stringify(want)) { bad += 1; console.error(`FAIL ${msg}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`); } };
const lectura = (pid, date, more) => Object.assign({ kind: "lectura", parcel_id: pid, fecha: date, text: date + ",8", sent_at: null }, more);
const dano = (pid, more) => Object.assign({ kind: "dano", parcel_id: pid, phone: "+520000000000", text: "Se me dañó el maíz por helada.", sent_at: null }, more);

// A thermometer reading never puts a number on «Daño»
let q = [lectura("P01", "2026-10-02")];
eq(Q.pendientes(q, "dano", "P01"), 0, "a waiting reading is not a damage report");
eq(Q.pendientes(q, "lectura", "P01"), 1, "a waiting reading counts on the thermometer tab");

// only this plot, and never the demo's own example report
q = [dano("P05"), dano("P01", { tour: true }), dano("P01"), dano("P01", { sent_at: "2026-10-03T12:00:00Z" })];
eq(Q.pendientes(q, "dano", "P01"), 1, "counts only this plot's unsent reports, without the demo's");
eq(Q.pendientes(q, "dano", "P05"), 1, "the other plot keeps its own count");
eq(Q.pendientes(q, "dano", "P02"), 0, "a plot with nothing waiting");

// what a row says after a try to send
const tried = (st) => { const it = dano("P01"); Q.anotar(it, st, "2026-10-03T12:00:00Z"); return it; };
eq(Q.estado(dano("P01")), "wait", "not tried yet");
eq(Q.estado(tried(null)), "wait", "no signal: it waits");
eq(Q.estado(tried(200)), "sent", "the server took it");
eq(tried(200).sent_at, "2026-10-03T12:00:00Z", "sent_at is written");
eq(Q.estado(tried(500)), "failed", "the server said no: said as failed");
eq(Q.estado(tried(422)), "failed", "a refused reading is said as failed");
for (const st of [404, 405, 501]) eq(Q.estado(tried(st)), "local", `no server (${st}): kept on this phone`);

// no server: nothing stays pending for ever; a refusal stays pending; a later success clears it
eq(Q.pendientes([tried(404), lectura("P01", "2026-10-02", { status: 404 })], "dano", "P01"), 0, "no server: no number on «Daño»");
eq(Q.pendientes([lectura("P01", "2026-10-02", { status: 405 })], "lectura", "P01"), 0, "no server: no number on the thermometer");
eq(Q.pendientes([tried(500)], "dano", "P01"), 1, "a refused report is still pending");
const again = tried(500); Q.anotar(again, 200, "2026-10-03T13:00:00Z");
eq([Q.estado(again), again.status, again.error], ["sent", undefined, undefined], "a later success clears the error");
const lost = tried(500); Q.anotar(lost, null, "x");
eq([Q.estado(lost), lost.status], ["wait", undefined], "signal lost after a refusal: it waits again");

// items saved by the page before this change only have the text of the error
eq(Q.estado(dano("P01", { error: "404" })), "local", "old item, 404 in the error text");
eq(Q.estado(dano("P01", { error: "500" })), "failed", "old item, 500 in the error text");
eq(Q.estado(dano("P01", { error: "Failed to fetch" })), "wait", "old item, network error");
eq(Q.pendientes([{ kind: "lectura", parcel_id: "P01", text: "2026-10-02,8", sent_at: null, error: "404" }], "dano", "P01"), 0, "the stuck number of the public link goes away");

// the row of a reading finds its queue item; a new reading for the same night replaces the one still waiting
q = [lectura("P01", "2026-10-01", { sent_at: "x" }), lectura("P01", "2026-10-02", { text: "2026-10-02,8" }), lectura("P02", "2026-10-02"),
     { kind: "lectura", parcel_id: "P01", text: "2026-09-30,-1.5", sent_at: null }];
eq(Q.deLectura(q, "P01", "2026-10-02").text, "2026-10-02,8", "finds the reading of that night");
eq(Q.deLectura(q, "P01", "2026-09-30").text, "2026-09-30,-1.5", "an item without `fecha` is found by its text");
eq(Q.deLectura(q, "P01", "2026-09-29"), null, "no queue item for that night");
const kept = Q.sinLecturaPendiente(q, "P01", "2026-10-02");
eq(kept.length, 3, "only the waiting reading of that plot and night is dropped");
eq(Q.sinLecturaPendiente(q, "P01", "2026-10-01").length, 4, "a reading already sent is kept");

console.log(`${n} checks, ${bad} failures`);
process.exit(bad ? 1 : 0);
