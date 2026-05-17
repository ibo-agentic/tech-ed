const CACHE = 'dipti-v1';

const PRECACHE = [
  '/',
  '/manifest.json',
];

// API paths — always go to network, never cache
const API_PATHS = ['/ask', '/ask-stream', '/ask-image', '/transcribe', '/login',
  '/signup', '/logout', '/verify-email', '/resend-verification',
  '/forgot-password', '/reset-password', '/student-opening', '/save-quiz-result',
  '/chats', '/projects'];

function isApiCall(url) {
  const path = new URL(url).pathname;
  return API_PATHS.some(p => path === p || path.startsWith(p + '/'));
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const { request } = event;

  // Only handle GET
  if (request.method !== 'GET') return;

  // Never cache API calls — let them fail naturally if offline
  if (isApiCall(request.url)) return;

  const url = new URL(request.url);

  // CDN resources (fonts, katex, mermaid) — cache-first
  if (url.origin !== self.location.origin) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE).then(cache => cache.put(request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Same-origin static assets — cache-first, update in background
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        const networkFetch = fetch(request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE).then(cache => cache.put(request, clone));
          }
          return response;
        });
        return cached || networkFetch;
      })
    );
    return;
  }

  // HTML pages — network-first, fall back to cache
  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE).then(cache => cache.put(request, clone));
        }
        return response;
      })
      .catch(() => caches.match(request))
  );
});
