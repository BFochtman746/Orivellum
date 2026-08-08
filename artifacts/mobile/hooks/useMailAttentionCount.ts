/**
 * useMailAttentionCount
 *
 * Polls GET /api/mail/summary every 30 s and returns the high_attention count
 * when the mail account is connected, 0 otherwise.
 *
 * Badge suppression contract:
 *   • Returns 0 immediately while the user is on any /mail* route — the badge
 *     is redundant when they are already looking at the queue.
 *   • Fires an immediate extra poll the moment the user LEAVES /mail so the
 *     count reflects the server state right away (not the next 30 s tick).
 *
 * Covered sub-routes (all start with '/mail'):
 *   /mail                 — attention list
 *   /mail/<id>            — message detail (also handles cold-start deep links)
 *   /mail/settings        — settings screen
 *   /mail/connect         — Outlook OAuth flow
 *   /mail/compose/<id>    — compose / reply screen
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { mobileFetch } from '@/lib/api';
import { usePathname } from 'expo-router';

const _DOMAIN   = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const _BASE_API = `https://${_DOMAIN}/api`;

export function useMailAttentionCount(): number {
  const [count, setCount] = useState(0);
  const path        = usePathname();
  const onMailRoute = path.startsWith('/mail');
  const prevOnMailRef = useRef(onMailRoute);

  const poll = useCallback(async () => {
    try {
      const r = await mobileFetch(`${_BASE_API}/mail/summary`);
      if (r.ok) {
        const data = await r.json();
        setCount(data.connected ? ((data.high_attention as number) ?? 0) : 0);
      }
    } catch {
      // silently fail — badge stays at last known value until next poll
    }
  }, []);

  // Regular 30 s polling interval
  useEffect(() => {
    poll();
    const t = setInterval(poll, 30_000);
    return () => clearInterval(t);
  }, [poll]);

  // Immediate re-poll when leaving the mail route so the badge is always fresh
  useEffect(() => {
    if (prevOnMailRef.current && !onMailRoute) {
      poll();
    }
    prevOnMailRef.current = onMailRoute;
  }, [onMailRoute, poll]);

  // Suppress badge while the user is viewing any mail screen
  return onMailRoute ? 0 : count;
}
