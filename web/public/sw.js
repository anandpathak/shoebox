// Shoebox service worker (LOCAL_APP_PLAN.md §9.1, §10.4).
//  - App shell (HTML/JS/CSS/icons): the page always loads, even offline.
//  - Thumbnails: cached as you scroll (cache-first; they never change for a photo id).
//  - Everything else under /api: straight to the network, never cached.
const VERSION = 'v1'
const SHELL = `shell-${VERSION}`
const THUMBS = 'thumbs-v1'
const MAX_THUMBS = 5000

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(['/', '/manifest.webmanifest', '/favicon.svg',
    '/icons/icon-192.png'])).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith('shell-') && k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()))
})

async function trimThumbs() {
  const cache = await caches.open(THUMBS)
  const keys = await cache.keys()
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_THUMBS))) await cache.delete(k)
}

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== location.origin) return

  if (/^\/api\/media\/[^/]+\/(thumb|poster)$/.test(url.pathname)) {
    event.respondWith(caches.open(THUMBS).then(async (cache) => {
      const hit = await cache.match(req)
      if (hit) return hit
      const res = await fetch(req)
      if (res.ok && res.type === 'basic') { cache.put(req, res.clone()); trimThumbs() }
      return res
    }))
    return
  }
  // Compressed copies saved for offline ("Save offline" in the viewer): served from the device when present.
  if (/^\/api\/media\/[^/]+\/compressed$/.test(url.pathname) && !url.search) {
    event.respondWith(caches.open('saved-v1').then(async (cache) => (await cache.match(url.pathname)) || fetch(req)))
    return
  }
  if (url.pathname.startsWith('/api/')) return                     // live data: network only

  if (req.mode === 'navigate') {                                    // the app page: network first
    event.respondWith(fetch(req).then((res) => {
      const copy = res.clone()
      caches.open(SHELL).then((c) => c.put('/', copy))
      return res
    }).catch(() => caches.match('/')))
    return
  }
  event.respondWith(caches.open(SHELL).then(async (cache) => {     // hashed assets and icons: cache first
    const hit = await cache.match(req)
    if (hit) return hit
    const res = await fetch(req)
    if (res.ok) cache.put(req, res.clone())
    return res
  }))
})

// Notifications: "Archive finished", "Original ready".
self.addEventListener('push', (event) => {
  let data = { title: 'Shoebox', body: '', url: '/#/library' }
  try { data = { ...data, ...event.data.json() } } catch { /* plain text */ }
  event.waitUntil(self.registration.showNotification(data.title, {
    body: data.body, icon: '/icons/icon-192.png', badge: '/icons/maskable-192.png', data: { url: data.url },
  }))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = (event.notification.data && event.notification.data.url) || '/#/library'
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
    const open = wins.find((w) => new URL(w.url).origin === location.origin)
    if (open) { open.navigate(url); return open.focus() }
    return self.clients.openWindow(url)
  }))
})
