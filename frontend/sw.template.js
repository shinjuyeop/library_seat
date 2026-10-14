const CACHE = 'library-shell-react-__BUILD_VERSION__';
const ASSETS = ['/', ..."__BUILD_ASSETS__", '/assets/icon.svg?v=5', '/assets/manifest.webmanifest', '/assets/apple-touch-icon.png?v=5', '/assets/icon-192.png?v=5', '/assets/icon-512.png?v=5', '/assets/favicon-32.png?v=5'];
const IMMUTABLE_ASSETS = new Set("__BUILD_ASSETS__");
const notificationTab = value => {
  try {
    const url = new URL(value || '/', self.location.origin);
    const tab = url.searchParams.get('tab');
    return url.origin === self.location.origin && ['my', 'schedule', 'settings'].includes(tab) ? tab : 'my';
  } catch { return 'my'; }
};
self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data?.json() || {}; } catch { /* Always show a visible notification. */ }
  event.waitUntil(self.registration.showNotification(String(payload.title || '도서관 좌석').slice(0, 80), {
    body: String(payload.body || '앱에서 좌석 상태를 확인해 주세요.').slice(0, 180),
    icon: '/assets/icon-192.png?v=5', badge: '/assets/icon-192.png?v=5',
    tag: String(payload.id || 'library-update').slice(0, 80),
    data: { tab: notificationTab(payload.url) },
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const tab = notificationTab('/?tab=' + event.notification.data?.tab);
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async clients => {
    const client = clients.find(item => new URL(item.url).origin === self.location.origin);
    if (client) {
      await client.focus();
      client.postMessage({ type: 'open-tab', tab });
      return;
    }
    await self.clients.openWindow('/?tab=' + tab);
  }));
});
self.addEventListener('install', event => { event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS))); self.skipWaiting(); });
self.addEventListener('activate', event => { event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('library-shell-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  // Credentials, account responses and all writes always go to the server.
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || !ASSETS.includes(url.pathname + url.search)) return;
  // Content-hashed JS/CSS cannot change at the same URL; avoid a network round trip.
  // HTML stays network-first so a new deployment can load its new asset URLs.
  if (IMMUTABLE_ASSETS.has(url.pathname + url.search)) {
    event.respondWith(caches.open(CACHE).then(async cache => {
      const cached = await cache.match(event.request);
      if (cached) return cached;
      const response = await fetch(event.request);
      if (response.ok) await cache.put(event.request, response.clone());
      return response;
    }));
    return;
  }
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); event.waitUntil(caches.open(CACHE).then(cache => cache.put(event.request, copy))); }
    return response;
  }).catch(() => caches.match(event.request)));
});
