/* cola.js: the rules of the phone's send queue (store and forward), with no screen and no network.
 *
 * A queue item is a thermometer reading (kind "lectura") or a damage report (kind "dano") that waits on the phone
 * until the Helada server takes it. What the screen says about an item, and what each tab counts, is decided here
 * so that tests/movil_cola.mjs can check it.
 * Works as a classic <script> (window.HeladaCola) and in Node (module.exports).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HeladaCola = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  // Answers of a host that has the page but no Helada server behind it (the static public link).
  const NO_SERVER = [404, 405, 501];

  /** HTTP status of the last try, or null. Items saved by an older page only have the text in `error`. */
  function status(it) {
    if (it.status != null) return it.status;
    const n = parseInt(it.error, 10);
    return Number.isNaN(n) ? null : n;
  }

  /** "sent"   the server has it
   *  "local"  there is no server to send it to: it stays on this phone, nothing is pending
   *  "failed" there was signal and the server said no (4xx, 5xx): it is tried again
   *  "wait"   not tried yet, or no signal: it is sent when there is signal */
  function estado(it) {
    if (it.sent_at) return "sent";
    const st = status(it);
    if (st == null) return "wait";
    return NO_SERVER.indexOf(st) >= 0 ? "local" : "failed";
  }

  /** Write down how a try to send ended. `st` is the HTTP status, or null when the request never got out. */
  function anotar(it, st, now) {
    if (st != null && st >= 200 && st < 300) { it.sent_at = now; delete it.status; delete it.error; return; }
    if (st == null) { delete it.status; it.error = "red"; return; }
    it.status = st; it.error = String(st);
  }

  /** What a tab's number counts: items of that kind, of that plot, that still have to go out. The demo's own
   *  example report (marked `tour`) is never counted. */
  function pendientes(q, kind, parcelId) {
    return q.filter((x) => x.kind === kind && x.parcel_id === parcelId && !x.tour && (estado(x) === "wait" || estado(x) === "failed")).length;
  }

  /** Night (evening date) a queued reading is for. Its text is «date,tmin_c». */
  const nocheDe = (it) => it.fecha || String(it.text || "").split(",")[0];

  /** The queue item of the reading written down for that plot and night: the latest one, or null. */
  function deLectura(q, parcelId, date) {
    for (let i = q.length - 1; i >= 0; i--) {
      const x = q[i];
      if (x.kind === "lectura" && x.parcel_id === parcelId && nocheDe(x) === date) return x;
    }
    return null;
  }

  /** A new reading for a night replaces the one still waiting for that night: only the last one is sent. */
  function sinLecturaPendiente(q, parcelId, date) {
    return q.filter((x) => !(x.kind === "lectura" && x.parcel_id === parcelId && nocheDe(x) === date && !x.sent_at));
  }

  return { NO_SERVER, status, estado, anotar, pendientes, deLectura, sinLecturaPendiente };
});
