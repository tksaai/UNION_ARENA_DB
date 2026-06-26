const VERSION = 'v6';
const SHELL_CACHE = `union-arena-shell-${VERSION}`;
const DATA_CACHE = `union-arena-data-${VERSION}`;
const IMAGE_CACHE = 'union-arena-card-images-v1';
const SHELL = [
  './',
  './index.html',
  './style.css',
  './app.js',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-180.png',
  './icons/icon-maskable-192.png',
  './icons/icon-maskable-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith('union-arena-') && ![SHELL_CACHE, DATA_CACHE, IMAGE_CACHE].includes(key)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  const scope = new URL(self.registration.scope);
  const isCardData = url.origin === scope.origin && url.pathname === `${scope.pathname}cards.json`;
  const isImage = /\.(?:png|jpe?g|webp|gif|svg)$/i.test(url.pathname);

  if (isCardData) {
    event.respondWith(networkFirst(event.request, DATA_CACHE));
  } else if (isImage) {
    event.respondWith(cacheFirst(event.request, IMAGE_CACHE));
  } else if (url.origin === scope.origin) {
    event.respondWith(staleWhileRevalidate(event.request, SHELL_CACHE));
  }
});

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok || response.type === 'opaque') await cache.put(request, response.clone());
    return response;
  } catch {
    return new Response('', { status: 504 });
  }
}

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch {
    return (await cache.match(request)) || new Response('[]', { headers: { 'Content-Type': 'application/json' } });
  }
}

async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const update = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => null);
  return cached || update || new Response('', { status: 504 });
}
