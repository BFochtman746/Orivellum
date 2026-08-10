/* Notification click handler — imported into the generated Workbox service
 * worker via workbox.importScripts (vite.config.ts). Deep-links a clicked
 * browser notification to the page stored in notification.data.url. */
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url;
  if (!url) return;
  event.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({
        type: 'window',
        includeUncontrolled: true,
      });
      for (const win of wins) {
        if ('focus' in win) {
          await win.focus();
          if ('navigate' in win) {
            try {
              await win.navigate(url);
            } catch (e) {
              /* cross-origin or detached client — opening a new window below */
            }
          }
          return;
        }
      }
      await self.clients.openWindow(url);
    })(),
  );
});
