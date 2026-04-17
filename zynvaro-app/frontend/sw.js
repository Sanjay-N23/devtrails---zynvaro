// Zynvaro Service Worker — PWA offline support
const CACHE = 'zynvaro-v14'; // BUMP VERSION ON EVERY DEPLOY — Phase 3 SOAR
const STATIC = ['/app', '/static/manifest.json', '/static/Zynvaro-bg-removed.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // Skip cache entirely for API calls
  if (e.request.url.includes('/auth/') || e.request.url.includes('/policies') ||
      e.request.url.includes('/triggers') || e.request.url.includes('/claims') ||
      e.request.url.includes('/analytics') || e.request.url.includes('/webhooks')) return;

  // Network-first for /app (always get fresh HTML, fall back to cache offline)
  if (e.request.url.endsWith('/app') || e.request.url.includes('/app?')) {
    e.respondWith(
      fetch(e.request).then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // Cache-first for other static assets (images, manifest)
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
