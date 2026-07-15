/* Service worker: cache the app shell so the PWA opens instantly (and offline
   after first visit). Network-first for navigations/assets so deploys arrive;
   /api is NEVER cached — data must be honest. Log-POST retries are handled by
   the IndexedDB outbox in the app (iOS has no Background Sync), not here. */
const CACHE = 'food-log-v1'

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(['/'])))
  self.skipWaiting()
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  )
  self.clients.claim()
})

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url)
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api')) return // data is never served stale

  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone()
        caches.open(CACHE).then((c) => c.put(e.request, copy))
        return res
      })
      .catch(() =>
        caches.match(e.request).then((hit) => hit ?? caches.match('/'))
      )
  )
})
