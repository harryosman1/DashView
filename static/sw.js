// DashView Service Worker — handles Web Push notifications
self.addEventListener('push', function(event) {
  if (!event.data) return;
  
  let data = {};
  try { data = event.data.json(); } catch(e) { data = {title: 'DashView', body: event.data.text()}; }
  
  const title = data.title || 'DashView';
  const options = {
    body: data.body || '',
    icon: '/icon.png',
    badge: '/icon.png',
    tag: data.tag || 'dashview',
    renotify: true,
    data: data.url ? {url: data.url} : {}
  };
  
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});

self.addEventListener('install', function(event) {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(clients.claim());
});
