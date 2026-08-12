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

/* ── Web Push (iPhone continuity core) ─────────────────────────────────────
 * Payloads are intentionally minimal — { id, kind, url } only, no content —
 * so nothing sensitive transits the push service. The notification body is
 * a generic label derived from `kind`; tapping deep-links via `url`.
 * tag = id gives OS-level dedupe against the in-app polling fallback. */
const PUSH_KIND_LABELS = {
  document_ready: 'A document finished processing',
  audiobook_ready: 'An audiobook render finished',
  chat_reply: 'Your AI reply is ready',
  task_done: 'A background task finished',
};

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    /* non-JSON payload — show the generic notification below */
  }
  const title = 'Orivellum';
  const body = PUSH_KIND_LABELS[data.kind] || 'Something finished in Orivellum';
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      tag: data.id || undefined,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      data: { url: data.url || '/' },
    }),
  );
});

/* The push service rotated our subscription — resubscribe with the same
 * VAPID key and tell the server, or pushes silently stop arriving. */
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(
    (async () => {
      try {
        const oldKey =
          event.oldSubscription && event.oldSubscription.options
            ? event.oldSubscription.options.applicationServerKey
            : null;
        if (!oldKey) return;
        const sub = await self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: oldKey,
        });
        await fetch('./api/system/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(sub.toJSON()),
        });
      } catch (e) {
        /* re-subscribe fails silently; the in-app polling fallback remains */
      }
    })(),
  );
});
