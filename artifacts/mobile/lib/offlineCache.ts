/**
 * Offline cache sync engine for Orivellum mobile.
 *
 * Proactively mirrors key server data (Works, knowledge items, conversations,
 * recent messages) into local AsyncStorage so the app can show meaningful
 * content when the server is unreachable.
 *
 * Call syncToCache() when:
 *   - The user first authenticates (via _layout.tsx initial-auth effect)
 *   - The app comes to the foreground (AppState 'active')
 *
 * Call queueMessage() when a send fails due to a network error.
 * Call flushMessageQueue() when connectivity is restored.
 *
 * Concurrency safety
 * ──────────────────
 * flushMessageQueue() uses a module-level single-flight lock (_flushPromise).
 * If a flush is already in progress (e.g. triggered simultaneously by the
 * layout's AppState handler and the chat recovery effect), the second caller
 * receives the same in-flight promise rather than re-entering the function.
 * This prevents duplicate sends entirely.
 *
 * Each outbox entry carries a stable `msgId` (timestamp + random suffix)
 * sent as `client_msg_id` in the POST body so the server can deduplicate
 * in the unlikely case a flush and network recovery coincide.
 */

import { mobileFetch } from './api';
import { readCache, writeCache } from './cache';
import { apiOrigin } from '@/lib/server';

const API_BASE = () => `${apiOrigin()}/api`;

export interface SyncResult {
  worksCount: number;
  conversationsCount: number;
  knowledgeSynced: number;
}

/**
 * Proactively sync key data to the local cache.
 * All fetches are best-effort — individual failures are swallowed so a
 * partial outage (e.g. slow knowledge endpoint) doesn't break the whole sync.
 */
export async function syncToCache(): Promise<SyncResult> {
  const result: SyncResult = { worksCount: 0, conversationsCount: 0, knowledgeSynced: 0 };

  // 1. Works ──────────────────────────────────────────────────────────────────
  let works: any[] = [];
  try {
    const res = await mobileFetch(`${API_BASE()}/works`);
    if (res.ok) {
      const body = await res.json();
      works = body.works ?? [];
      await writeCache('works:list', works);
      result.worksCount = works.length;
    }
  } catch {}

  // 2. Top 200 knowledge items per Work (up to 10 works, in parallel) ────────
  if (works.length > 0) {
    await Promise.all(
      works.slice(0, 10).map(async (w: any) => {
        if (!w.id) return;
        try {
          const res = await mobileFetch(`${API_BASE()}/works/${w.id}/knowledge?limit=200`);
          if (res.ok) {
            const body = await res.json();
            const items: any[] = body.knowledge ?? body.items ?? body.knowledge_items ?? [];
            await writeCache(`work:${w.id}:knowledge`, items);
            result.knowledgeSynced += items.length;
          }
        } catch {}
      })
    );
  }

  // 3. Last 50 conversations ──────────────────────────────────────────────────
  let conversations: any[] = [];
  try {
    const res = await mobileFetch(`${API_BASE()}/conversations?archived=false&limit=50`);
    if (res.ok) {
      const body = await res.json();
      conversations = body.conversations ?? [];
      await writeCache('conversations:list', conversations);
      result.conversationsCount = conversations.length;
    }
  } catch {}

  // 4. Messages for the 10 most recent conversations ─────────────────────────
  if (conversations.length > 0) {
    await Promise.all(
      conversations.slice(0, 10).map(async (c: any) => {
        if (!c.id) return;
        try {
          const res = await mobileFetch(`${API_BASE()}/conversations/${c.id}`);
          if (res.ok) {
            const body = await res.json();
            const messages: any[] = body.messages ?? [];
            await writeCache(`conversation:${c.id}:messages`, messages);
          }
        } catch {}
      })
    );
  }

  return result;
}

// ── Outbox / offline message queue ──────────────────────────────────────────

interface OutboxEntry {
  /** Stable client-generated ID used as an idempotency key on retry. */
  msgId: string;
  convId: string;
  text: string;
  ts: number;
}

/** Generate a simple stable ID for a new outbox entry. */
function _newMsgId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

// ── Concurrency controls ─────────────────────────────────────────────────────
//
// Two locks protect the shared outbox AsyncStorage key:
//
// 1. _outboxMutex (promise chain): serializes ALL outbox reads and writes
//    so queueMessage() can never race with the flush's final writeCache call.
//    Every function that touches the outbox must enter _withOutboxLock().
//
//    Design: each entry into _withOutboxLock() chains onto the current tail.
//    On both success and failure the outer promise resolves to void so the
//    chain never stalls.  The actual result is on the inner promise returned
//    to the caller.
//
// 2. _flushPromise (single-flight): collapses concurrent flushMessageQueue()
//    callers (layout foreground handler + chat recovery effect) into one
//    in-progress promise so no message is POSTed twice.

let _outboxMutex: Promise<void> = Promise.resolve();

function _withOutboxLock<T>(fn: () => Promise<T>): Promise<T> {
  const result = _outboxMutex.then(fn);
  // Always advance the chain — never let an error stall the mutex.
  _outboxMutex = result.then(
    () => {},
    () => {},
  );
  return result;
}

let _flushPromise: Promise<number> | null = null;

/**
 * Add a user message to the offline outbox.
 * Runs under _outboxMutex so a concurrent flush cannot overwrite this entry
 * by persisting a stale "remaining" snapshot.
 *
 * @param convId  Target conversation ID.
 * @param text    Message text.
 * @param msgId   Optional pre-generated stable ID.  Pass the same ID that was
 *                put on the optimistic bubble so outbox entry and bubble share
 *                a key for msgId-based reconciliation after flush.
 *                When omitted a new ID is generated.
 */
export function queueMessage(
  convId: string,
  text: string,
  msgId?: string,
): Promise<void> {
  return _withOutboxLock(async () => {
    const entry = await readCache<OutboxEntry[]>('outbox');
    const queue: OutboxEntry[] = entry?.data ?? [];
    queue.push({ msgId: msgId ?? _newMsgId(), convId, text, ts: Date.now() });
    await writeCache('outbox', queue);
  });
}

/**
 * Flush all queued outbox messages to the server.
 *
 * Concurrency:
 * - Single-flight (_flushPromise): concurrent callers share one in-flight
 *   Promise so no entry is POSTed more than once per flush cycle.
 * - The outbox snapshot read and the final write-back both run inside
 *   _withOutboxLock.  Any queueMessage() that arrives while network I/O is
 *   in progress runs after the snapshot lock but before the write-back lock,
 *   so its entry is never silently overwritten — the write-back logic merges
 *   entries added since the snapshot was taken.
 *
 * Returns the number of messages successfully sent.
 * Unsent messages remain in the outbox for the next attempt.
 */
export function flushMessageQueue(): Promise<number> {
  if (_flushPromise) {
    return _flushPromise;
  }
  _flushPromise = _doFlush().finally(() => {
    _flushPromise = null;
  });
  return _flushPromise;
}

async function _doFlush(): Promise<number> {
  // Take a snapshot under the mutex — no other outbox writer runs during this read.
  const snapshot = await _withOutboxLock(async () => {
    const entry = await readCache<OutboxEntry[]>('outbox');
    return (entry?.data ?? []) as OutboxEntry[];
  });

  if (!snapshot.length) return 0;

  let sent = 0;
  const failed: OutboxEntry[] = [];

  // Network I/O runs outside the lock — no one is blocked while we wait.
  for (const msg of snapshot) {
    try {
      const res = await mobileFetch(
        `${API_BASE()}/conversations/${msg.convId}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: msg.text,
            stream: false,
            // Server persists this in messages.client_msg_id (schema v86)
            // and uses INSERT OR IGNORE + returns the original AI reply on
            // retry, so lost-response retries are fully idempotent.
            client_msg_id: msg.msgId,
          }),
        },
      );
      if (res.ok) {
        sent++;
      } else {
        failed.push(msg);
      }
    } catch {
      failed.push(msg);
    }
  }

  // Write back under the mutex.  Entries queued while we were doing I/O
  // will be present in the live outbox but not in our snapshot; we preserve
  // them by merging rather than blindly overwriting.
  const snapshotIds = new Set(snapshot.map((m) => m.msgId));
  await _withOutboxLock(async () => {
    const current = await readCache<OutboxEntry[]>('outbox');
    const live: OutboxEntry[] = current?.data ?? [];
    // Keep: entries added after our snapshot + our own failures.
    const addedSinceSnapshot = live.filter((m) => !snapshotIds.has(m.msgId));
    await writeCache('outbox', [...failed, ...addedSinceSnapshot]);
  });

  return sent;
}

/**
 * Return pending outbox entries for one conversation.
 * Used by the chat screen to hydrate queued bubbles on mount and after flush.
 */
export async function getOutboxForConversation(
  convId: string,
): Promise<Array<{ msgId: string; convId: string; text: string; ts: number }>> {
  const entry = await readCache<OutboxEntry[]>('outbox');
  if (!entry?.data?.length) return [];
  return entry.data.filter((m) => m.convId === convId);
}

/**
 * Return the number of pending outbox messages for a given conversation.
 */
export async function pendingQueueCount(convId: string): Promise<number> {
  const pending = await getOutboxForConversation(convId);
  return pending.length;
}
