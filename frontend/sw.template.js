const CACHE = 'library-shell-react-__BUILD_VERSION__';
const ASSETS = ['/', ..."__BUILD_ASSETS__", '/assets/icon.svg?v=5', '/assets/manifest.webmanifest', '/assets/apple-touch-icon.png?v=5', '/assets/icon-192.png?v=5', '/assets/icon-512.png?v=5', '/assets/favicon-32.png?v=5'];
self.addEventListener('install', event => { event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS))); self.skipWaiting(); });
self.addEventListener('activate', event => { event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('library-shell-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  // Credentials, account responses and all writes always go to the server.
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || !ASSETS.includes(url.pathname + url.search)) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); event.waitUntil(caches.open(CACHE).then(cache => cache.put(event.request, copy))); }
    return response;
  }).catch(() => caches.match(event.request)));
});
