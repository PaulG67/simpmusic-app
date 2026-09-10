/* Service worker: app shell, offline audio and artwork.

   The Cache API cannot answer byte-range requests, but <audio> always asks for
   ranges - so downloaded tracks are stored as one complete response and sliced
   here into 206 replies. */

const SHELL_CACHE = "simpmusic-shell-v2";
const AUDIO_CACHE = "simpmusic-audio-v1";
const COVER_CACHE = "simpmusic-covers-v1";

const SHELL_FILES = [
  "/",
  "/static/styles.css",
  "/static/app.js",
  "/static/player.js",
  "/static/offline.js",
  "/static/eq.js",
  "/static/icons/icon.svg",
  "/manifest.webmanifest",
];

const KEEP = [SHELL_CACHE, AUDIO_CACHE, COVER_CACHE];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_FILES))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => !KEEP.includes(key)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

async function sliceRange(cached, rangeHeader) {
  const buffer = await cached.arrayBuffer();
  const size = buffer.byteLength;
  const match = /bytes=(\d*)-(\d*)/.exec(rangeHeader || "");

  let start = 0;
  let end = size - 1;

  if (match) {
    if (match[1] === "" && match[2] !== "") {
      start = Math.max(size - parseInt(match[2], 10), 0);
    } else {
      if (match[1] !== "") start = parseInt(match[1], 10);
      if (match[2] !== "") end = parseInt(match[2], 10);
    }
  }
  end = Math.min(end, size - 1);

  if (start > end || start >= size) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
  }

  const slice = buffer.slice(start, end + 1);
  return new Response(slice, {
    status: 206,
    headers: {
      "Content-Type": cached.headers.get("content-type") || "audio/mp4",
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Content-Length": String(slice.byteLength),
      "Accept-Ranges": "bytes",
    },
  });
}

async function handleAudio(request) {
  const cache = await caches.open(AUDIO_CACHE);
  const cached = await cache.match(request.url);

  if (cached) {
    const range = request.headers.get("range");
    return range ? sliceRange(cached.clone(), range) : cached;
  }

  return fetch(request);
}

async function handleCover(request) {
  const cache = await caches.open(COVER_CACHE);
  const cached = await cache.match(request.url);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request.url, response.clone());
    return response;
  } catch (error) {
    return new Response(null, { status: 504 });
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/api/stream/")) {
    event.respondWith(handleAudio(request));
    return;
  }
  if (url.pathname === "/api/cover") {
    event.respondWith(handleCover(request));
    return;
  }
  // Everything else under /api and /rest must stay live.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/rest/")) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && SHELL_FILES.includes(url.pathname)) {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || caches.match("/")))
  );
});
