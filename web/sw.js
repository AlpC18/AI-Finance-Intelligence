/* Offline shell only. Financial API responses are never cached: stale market,
   portfolio, or order data must fail visibly rather than look authoritative. */
const CACHE = "afi-shell-v1";
const SHELL = ["/", "/static/app.css", "/static/app.js", "/static/manifest.webmanifest"];

self.addEventListener("install", event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith(caches.match(event.request).then(hit => hit || fetch(event.request)));
});
