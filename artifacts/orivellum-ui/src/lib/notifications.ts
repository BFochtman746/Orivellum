/** Browser-notification helpers (opt-in, per-device).
 *
 * The enable toggle lives in localStorage because notification permission is
 * itself per-browser — a server-side setting would wrongly apply to every
 * device. The System page card flips the toggle and requests permission.
 */

const ENABLED_KEY = 'orv-browser-alerts';
const CURSOR_KEY = 'orv-notif-cursor';

export function notificationsSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}

export function alertsEnabled(): boolean {
  try {
    return localStorage.getItem(ENABLED_KEY) === 'true';
  } catch {
    return false;
  }
}

export function setAlertsEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(ENABLED_KEY, enabled ? 'true' : 'false');
  } catch {
    /* private mode — toggle just won't persist */
  }
}

/** Must be called from a user gesture (the settings switch). */
export async function requestNotificationPermission(): Promise<NotificationPermission> {
  if (!notificationsSupported()) return 'denied';
  if (Notification.permission !== 'default') return Notification.permission;
  try {
    return await Notification.requestPermission();
  } catch {
    return 'denied';
  }
}

export interface NotifCursor {
  bootId: string;
  lastId: number;
}

export function loadCursor(): NotifCursor | null {
  try {
    const raw = localStorage.getItem(CURSOR_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (typeof parsed?.bootId === 'string' && typeof parsed?.lastId === 'number') {
      return parsed as NotifCursor;
    }
  } catch {
    /* corrupted — start fresh */
  }
  return null;
}

export function saveCursor(cursor: NotifCursor): void {
  try {
    localStorage.setItem(CURSOR_KEY, JSON.stringify(cursor));
  } catch {
    /* non-fatal */
  }
}

/** Show a browser notification, preferring the service-worker registration
 * (survives tab backgrounding on all platforms; page-constructed
 * Notifications throw on Android). `url` is app-relative ("/library/abc"). */
export async function showBrowserNotification(
  title: string,
  body: string,
  url: string,
  eventId?: number,
): Promise<void> {
  if (!notificationsSupported() || Notification.permission !== 'granted') return;
  const base = (import.meta.env.BASE_URL ?? '/').replace(/\/$/, '');
  const absoluteUrl = url ? `${base}${url}` : `${base}/`;
  const icon = `${base}/icon-192.png`;
  // Per-event tag: same-tag notifications REPLACE each other, so tagging by
  // URL would collapse several completions in one poll into a single alert.
  const tag = eventId != null ? `orv-evt-${eventId}` : undefined;
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    if (reg) {
      await reg.showNotification(title, {
        body,
        icon,
        data: { url: absoluteUrl },
        tag,
      });
      return;
    }
  } catch {
    /* fall through to page notification */
  }
  try {
    const n = new Notification(title, { body, icon });
    n.onclick = () => {
      window.focus();
      window.location.href = absoluteUrl;
    };
  } catch {
    /* platform requires SW notifications and none is registered (dev mode) */
  }
}
