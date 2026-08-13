/** Polls GET /api/system/notifications and surfaces new events:
 *  - tab hidden  → browser Notification (deep-links via the SW click handler)
 *  - tab visible → sonner toast (the user is already looking at the app)
 *
 * Mounted once in App.tsx. The cursor (server boot id + last-seen event id)
 * persists in localStorage so reloads never replay old alerts, and a server
 * restart (new boot id) fast-forwards instead of re-notifying.
 */
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { apiFetch } from '@/lib/auth';
import {
  alertsEnabled,
  loadCursor,
  saveCursor,
  showBrowserNotification,
} from '@/lib/notifications';

const POLL_MS = 15_000;

interface FeedEvent {
  id: number;
  kind: string;
  title: string;
  body: string;
  url: string;
  created_at: number;
}

interface FeedResponse {
  boot_id: string;
  latest_id: number;
  notifications: FeedEvent[];
}

export function useBrowserNotifications(): void {
  const busyRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const base = (import.meta.env.BASE_URL ?? '/').replace(/\/$/, '');

    const doPoll = async () => {
      try {
        const cursor = loadCursor();
        const after = cursor?.lastId ?? 0;
        const resp = await apiFetch(
          `${base}/api/system/notifications?after=${after}`,
        );
        if (!resp.ok || cancelled) return;
        const data: FeedResponse = await resp.json();

        // First run or server restarted: sync the cursor silently — never
        // replay alerts for work that finished before we started watching.
        if (!cursor || cursor.bootId !== data.boot_id) {
          saveCursor({ bootId: data.boot_id, lastId: data.latest_id });
          return;
        }

        for (const ev of data.notifications) {
          if (!alertsEnabled()) break;
          if (document.hidden) {
            void showBrowserNotification(ev.title, ev.body, ev.url, ev.id);
          } else {
            toast.success(ev.title, { description: ev.body, duration: 8000 });
          }
        }
        saveCursor({ bootId: data.boot_id, lastId: data.latest_id });
      } catch {
        /* offline / server down — the connectivity ribbon already reports it */
      }
    };

    const poll = async () => {
      if (busyRef.current || cancelled) return;
      // WP5: no background network work while the tab is hidden — the
      // visibilitychange handler below catches up the moment it returns.
      if (document.hidden) return;
      busyRef.current = true;
      try {
        // Cross-tab mutual exclusion: with several tabs open, only the one
        // that wins the Web Lock consumes (and presents) each event — the
        // read-cursor/save-cursor sequence is not atomic across tabs
        // otherwise, and every tab would alert for every event.
        if (navigator.locks?.request) {
          await navigator.locks.request(
            'orv-notif-poll',
            { ifAvailable: true },
            async (lock) => {
              if (lock) await doPoll();
            },
          );
        } else {
          await doPoll();
        }
      } catch {
        /* lock API failure — skip this cycle rather than double-alert */
      } finally {
        busyRef.current = false;
      }
    };

    void poll();
    const interval = setInterval(poll, POLL_MS);
    // Catch up immediately when the user returns to the tab.
    const onVisible = () => {
      if (!document.hidden) void poll();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);
}
