/* Offline shell for Helada on the phone.
 *
 * One version, all files together. Install copies the page, the model files and both packs into a cache named
 * after VERSION (about 1 MB). The page is then served ONLY from that cache, so it can never show a mix of old
 * and new files. An update is a new VERSION: the browser sees this file changed, installs the whole new set,
 * switches over, and the page reloads once (see movil.js).
 *
 * VERSION is a hash of the shell files, written by scripts/build_movil_pack.py (--stamp). A test fails if any
 * shell file changes without a new stamp.
 *
 * On a developer's machine (registered as sw.js?dev=1) files come from the network first, so edits show at once;
 * the cache is only the fallback when the network is off.
 *
 * The forecast service and the Helada server API are never cached here: the page stores what it needs itself.
 */
const VERSION = "helada-movil-2f15ea953c71";
const SHELL = [
  "index.html", "movil.css", "movil.js", "helada-model.js", "cola.js", "i18n.js", "manifest.webmanifest", "icon.svg", "fonts/nunito.woff2",
  "data/pack.json", "data/pack-demo.json",
  "data/models/model_anchor.txt.gz", "data/models/model_transfer_wx.txt.gz", "data/models/model_transfer_terrain.txt.gz",
  "data/models-demo/model_anchor.txt.gz", "data/models-demo/model_transfer_wx.txt.gz", "data/models-demo/model_transfer_terrain.txt.gz",
];
const DEV = new URL(self.location).searchParams.has("dev");
const BASE = new URL("./", self.location).pathname;

self.addEventListener("install", (e) => {
  // cache: "reload" skips the browser's HTTP cache, so the set is what the server has now
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "reload" })))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin || !url.pathname.startsWith(BASE)) return;
  e.respondWith((async () => {
    const cache = await caches.open(VERSION);
    // a request for the folder itself is the page (some static servers do not serve an index for it)
    const key = url.pathname.endsWith("/") ? new Request(new URL("index.html", url).href) : e.request;
    const cached = () => cache.match(key, { ignoreSearch: true });
    if (DEV) {
      try { const r = await fetch(e.request, { cache: "no-store" }); if (r.ok) return r; } catch (err) { /* offline: fall through */ }
      return (await cached()) || new Response("Sin conexión", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }
    return (await cached()) || fetch(e.request);
  })());
});
