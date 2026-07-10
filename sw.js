// GoldPremium.org — minimal service worker
//
// Purpose: PWA installability only (Chrome requires a registered service
// worker with a fetch handler before it will offer "Install app").
//
// Deliberately does NOT cache anything. Prices come live from
// api.goldpremium.org and must never be served stale from a cache.
// This SW is a pure network passthrough.

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Passthrough — always go to the network, never intercept/cache.
  event.respondWith(fetch(event.request));
});
