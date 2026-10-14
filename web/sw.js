// Push-only service worker: shows notifications and opens the app when one is tapped.
// It deliberately has no fetch handler, so it never caches pages or data (nothing can go stale).
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || 'Portfolio Tracker', {
    body: data.body || '',
    icon: 'icons/icon-192.png',
    badge: 'icons/icon-192.png',
    data: { url: data.url || '/' },
  }));
});

// Take control of open app windows right away, so a tapped notification can move them to the Alerts page.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || '/', self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const open = windows.find((w) => new URL(w.url).origin === self.location.origin);
    if (open) {
      await open.focus();
      try { return await open.navigate(url); } catch { return undefined; } // not controlled yet: focusing is enough
    }
    return self.clients.openWindow(url);
  })());
});
