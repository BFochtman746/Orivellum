/**
 * Outbox hooks — queue state for the sync chip and the app-wide flusher.
 *
 * `useOutboxState()`  — live counts (queued/failed) + flushing flag + online.
 * `useOutboxSync()`   — mount ONCE (app frame). Flushes the persistent outbox
 *                       strictly in order on reconnect, foreground, and a slow
 *                       interval. Chat ops replay through the non-streaming
 *                       endpoint with their stable `client_msg_id`, so the
 *                       server's idempotency claim makes delivery exactly-once.
 */

import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  getGetConversationQueryKey,
  getListConversationsQueryKey,
} from "@workspace/api-client-react";

import { apiFetch } from "@/lib/auth";
import {
  type ApiCallPayload,
  type ChatMessagePayload,
  type OutboxOp,
  flushOutbox,
  isFlushing,
  listOps,
  subscribeOutbox,
} from "@/lib/outbox";

const API_BASE = `${import.meta.env.BASE_URL?.replace(/\/$/, "") || ""}/api`;
const FLUSH_INTERVAL_MS = 20_000;

export interface OutboxState {
  queued: number;
  failed: number;
  flushing: boolean;
  online: boolean;
}

export function useOutboxState(): OutboxState {
  const [state, setState] = useState<OutboxState>({
    queued: 0,
    failed: 0,
    flushing: false,
    online: typeof navigator === "undefined" ? true : navigator.onLine,
  });

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const ops = await listOps();
        if (!alive) return;
        setState({
          queued: ops.filter((o) => o.state === "queued" || o.state === "sending").length,
          failed: ops.filter((o) => o.state === "failed").length,
          flushing: isFlushing(),
          online: navigator.onLine,
        });
      } catch {
        /* IDB unavailable (private mode) — chip falls back to connectivity only */
      }
    };
    refresh();
    const unsub = subscribeOutbox(refresh);
    const onNet = () => refresh();
    window.addEventListener("online", onNet);
    window.addEventListener("offline", onNet);
    return () => {
      alive = false;
      unsub();
      window.removeEventListener("online", onNet);
      window.removeEventListener("offline", onNet);
    };
  }, []);

  return state;
}

/** HTTP status → flush verdict. Client errors are final; everything else retries. */
function verdictFromStatus(status: number): "delivered" | "retry" | "failed" {
  if (status >= 200 && status < 300) return "delivered";
  if (status === 408 || status === 409 || status === 429) return "retry";
  if (status >= 400 && status < 500) return "failed";
  return "retry";
}

export function useOutboxSync(): void {
  const queryClient = useQueryClient();

  const flush = useCallback(async () => {
    if (!navigator.onLine) return;
    await flushOutbox({
      chat_message: async (op: OutboxOp) => {
        const p = op.payload as ChatMessagePayload;
        const r = await apiFetch(`${API_BASE}/conversations/${p.convId}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: p.text,
            stream: false,
            deep: p.deep,
            scope: p.scope,
            image_b64: p.image_b64,
            image_media_type: p.image_media_type,
            client_msg_id: op.opId,
          }),
        });
        const verdict = verdictFromStatus(r.status);
        if (verdict === "delivered") {
          queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(p.convId) });
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
        }
        return verdict;
      },
      api_call: async (op: OutboxOp) => {
        const p = op.payload as ApiCallPayload;
        const r = await apiFetch(p.url, {
          method: p.method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(p.body),
        });
        return verdictFromStatus(r.status);
      },
    });
  }, [queryClient]);

  useEffect(() => {
    // Flush on mount (app launch with a persisted queue), on reconnect, on
    // foreground, and on a slow safety interval.
    flush();
    const onOnline = () => flush();
    const onVisible = () => {
      if (document.visibilityState === "visible") flush();
    };
    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVisible);
    const timer = setInterval(() => flush(), FLUSH_INTERVAL_MS);
    return () => {
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVisible);
      clearInterval(timer);
    };
  }, [flush]);
}
