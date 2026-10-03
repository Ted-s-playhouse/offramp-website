/* OffRamp REI deal room service worker (Master Task File item 16).
   Shell + icons are cached so the installed app opens offline; API and
   everything else stay network-only so no stale data is ever shown. */
const VERSION = 'offramp-shell-v15-2026-10-03';
const SHELL = ['/app/', '/app/manifest.webmanifest', '/app/icons/icon-192.png', '/app/icons/icon-512.png', '/brand/mark_ramp.png'];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(VERSION).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== VERSION; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener('fetch', function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/internal/')) return; // always live
  var isShell = SHELL.indexOf(url.pathname) >= 0 || url.pathname === '/app/index.html';
  if (!isShell) return;
  // network first, fall back to the cached shell when offline
  e.respondWith(fetch(e.request).then(function (res) {
    var copy = res.clone(); caches.open(VERSION).then(function (c) { c.put(e.request, copy); }); return res;
  }).catch(function () { return caches.match(e.request, { ignoreSearch: true }); }));
});
