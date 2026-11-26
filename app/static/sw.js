const CACHE = "valhar-static-v19";
const PRECACHE = [
  "/static/css/fonts.css?v=19",
  "/static/css/tokens.css?v=19",
  "/static/css/app.css?v=19",
  "/static/css/chronology.css?v=19",
  "/static/css/structure.css?v=19",
  "/static/css/lore.css?v=19",
  "/static/css/monde.css?v=19",
  "/static/css/fiches.css?v=19",
  "/static/app.js?v=19",
  "/static/vendor/htmx.min.js?v=2.0.4",
  "/static/fonts/cinzel-latin-wght-normal.woff2",
  "/static/fonts/eb-garamond-latin-wght-normal.woff2",
  "/static/fonts/eb-garamond-latin-wght-italic.woff2",
  "/static/fonts/inter-latin-wght-normal.woff2",
  "/static/offline.html",
  "/static/icons/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/static/offline.html")));
  }
});
