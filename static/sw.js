// sw.js
// Minimal service worker for the LIFF form app. iOS Safari's
// background-fetch support is limited (it cannot wake the worker
// up periodically), so the work this SW does is limited to:
//
//   - Cache-first response for the app shell and the iframe
//     payloads so the home-screen app is responsive when the
//     device is offline.
//   - network-first for the /api/* endpoints so live data still
//     wins when the network is up.
//
// Note on iOS Web Push: Safari 16.4+ accepts push subscriptions
// for home-screen apps, but only with explicit user gesture and
// a server push key. We do not ship that integration here because
// the form-app is read/write inside the corporate Render service
// and an iOS push would need a separate APNs + Web-Push bridge
// that is out of scope for this iteration.

const CACHE_NAME = 'liff-form-v1';
const APP_SHELL = [
  '/',
  '/form.html',
  '/index.html',
  '/manifest.json',
  '/bike/',
  '/bento/',
  '/weather/',
  '/route/',
  '/sdk.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL).catch(() => {
      // addAll rejects if any of the URLs are unreachable. We do not
      // want a flaky Render cold start to brick the install, so
      // swallow the error; the fetch handler will fall through to
      // the network on the next request and repopulate the cache.
    }))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  // Bypass the SW for any cross-origin request (LINE SDK, tile
  // servers, etc.). They have their own caches and we should not
  // intercept.
  if (url.origin !== self.location.origin) return;
  // /api/* always prefers the network so live data is fresh.
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
    return;
  }
  // App shell: cache-first with network fallback.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((resp) => {
        // Only cache successful basic responses; opaque / errored
        // responses would just bloat the cache.
        if (resp && resp.status === 200 && resp.type === 'basic') {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }
        return resp;
      }).catch(() => {
        // Last-resort fallback for navigations: serve the host
        // page so the user can at least see the offline message
        // rather than the browser's default "no internet" page.
        if (event.request.mode === 'navigate') return caches.match('/');
        return new Response('', { status: 504 });
      });
    })
  );
});
