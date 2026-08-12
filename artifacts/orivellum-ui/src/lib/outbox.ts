/**
 * Persistent client operation outbox (IndexedDB) — iPhone continuity core.
 *
 * Every mutable client action (chat message, draft save, approval) is written
 * here with a stable client-generated op id BEFORE any network attempt.  If
 * the attempt fails (dead zone, iOS suspension, server restart) the op stays
 * queued and is flushed — oldest first — when connectivity returns.  Chat ops
 * reuse the op id as `client_msg_id`, so the server's idempotency claim makes
 * replays exactly-once; the other ops are idempotent PATCH/PUTs.
 *
 * IndexedDB (not localStorage) because iOS Safari evicts localStorage less
 * predictably under memory pressure and IDB handles structured payloads
 * (images) without JSON size blowups.
 */

import { randomUUID } from "@/lib/uuid";

const DB_NAME = "orivellum-outbox";
const DB_VERSION = 1;
const STORE = "ops";

export type OutboxState = "queued" | "sending" | "delivered" | "failed";

/** True when an error means the request never reached the server (offline,
 *  DNS failure, dropped socket before any response) — vs a server error. */
export function isNetworkError(err: unknown): boolean {
  if (err instanceof TypeError) return true;
  const msg = err instanceof Error ? err.message : String(err);
  return /failed to fetch|networkerror|load failed|network request failed|connection/i.test(msg);
}

export interface ChatMessagePayload {
  convId: string;
  text: string;
  deep: boolean;
  scope: "work" | "all";
  image_b64?: string;
  image_media_type?: string;
}

export interface ApiCallPayload {
  method: "PATCH" | "PUT" | "POST";
  url: string; // full URL including API base
  body: unknown;
  /** Human label for the queue UI, e.g. "Draft save" */
  label: string;
}

export interface OutboxOp {
  /** Stable id generated before any network attempt; doubles as client_msg_id. */
  opId: string;
  type: "chat_message" | "api_call";
  /** Latest-wins key: enqueueing with the same replaceKey replaces the old op. */
  replaceKey?: string;
  payload: ChatMessagePayload | ApiCallPayload;
  state: OutboxState;
  createdAt: number;
  attempts: number;
  lastError?: string;
}

// ── IDB plumbing ─────────────────────────────────────────────────────────────

let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "opId" });
        store.createIndex("createdAt", "createdAt");
        store.createIndex("replaceKey", "replaceKey");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB open failed"));
  });
  return dbPromise;
}

function tx<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(STORE, mode);
        const req = fn(t.objectStore(STORE));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error ?? new Error("IndexedDB request failed"));
      })
  );
}

// ── Change subscription (drives the queue badge / sync chip) ────────────────

type Listener = () => void;
const listeners = new Set<Listener>();

function notify() {
  listeners.forEach((l) => {
    try {
      l();
    } catch {
      /* listener errors must not break the outbox */
    }
  });
}

export function subscribeOutbox(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// ── Core API ─────────────────────────────────────────────────────────────────

// Monotonic creation timestamps: getAll() returns ops in opId order, and two
// ops enqueued within the same millisecond would tie on Date.now() — letting
// a later op overtake an earlier one after the createdAt sort. Strictly
// increasing timestamps make flush order equal enqueue order.
let lastCreatedAt = 0;
function nextCreatedAt(): number {
  lastCreatedAt = Math.max(Date.now(), lastCreatedAt + 1);
  return lastCreatedAt;
}

/** Persist an op BEFORE any network attempt. Returns the stable op id. */
export async function enqueueOp(
  type: OutboxOp["type"],
  payload: OutboxOp["payload"],
  opts: { opId?: string; replaceKey?: string } = {}
): Promise<string> {
  const opId = opts.opId ?? randomUUID();
  if (opts.replaceKey) {
    // Latest-wins: drop any older queued op with the same key (e.g. draft
    // saves for the same document — only the newest content matters).
    const all = await listOps();
    for (const op of all) {
      if (op.replaceKey === opts.replaceKey && op.state !== "delivered") {
        await tx("readwrite", (s) => s.delete(op.opId));
      }
    }
  }
  const op: OutboxOp = {
    opId,
    type,
    replaceKey: opts.replaceKey,
    payload,
    state: "queued",
    createdAt: nextCreatedAt(),
    attempts: 0,
  };
  await tx("readwrite", (s) => s.put(op));
  notify();
  return opId;
}

export async function getOp(opId: string): Promise<OutboxOp | undefined> {
  return tx<OutboxOp | undefined>("readonly", (s) => s.get(opId) as IDBRequest<OutboxOp | undefined>);
}

export async function listOps(): Promise<OutboxOp[]> {
  const ops = await tx<OutboxOp[]>("readonly", (s) => s.getAll() as IDBRequest<OutboxOp[]>);
  return ops.sort((a, b) => a.createdAt - b.createdAt);
}

export async function listPendingOps(): Promise<OutboxOp[]> {
  return (await listOps()).filter((op) => op.state === "queued" || op.state === "failed");
}

export async function countQueued(): Promise<number> {
  return (await listOps()).filter((op) => op.state === "queued").length;
}

export async function markOpState(opId: string, state: OutboxState, error?: string): Promise<void> {
  const op = await getOp(opId);
  if (!op) return;
  const next: OutboxOp = {
    ...op,
    state,
    attempts: state === "sending" ? op.attempts + 1 : op.attempts,
    lastError: error ?? (state === "delivered" ? undefined : op.lastError),
  };
  await tx("readwrite", (s) => s.put(next));
  notify();
}

/** Delivery confirmed — the op has served its purpose and is removed. */
export async function removeOp(opId: string): Promise<void> {
  await tx("readwrite", (s) => s.delete(opId));
  notify();
}

// ── Flush ────────────────────────────────────────────────────────────────────

export type OpHandler = (op: OutboxOp) => Promise<"delivered" | "retry" | "failed">;

let flushing = false;

export function isFlushing(): boolean {
  return flushing;
}

/**
 * Flush queued ops oldest-first, strictly in order.  Stops at the first
 * "retry" result (still offline / server busy) so ordering is preserved —
 * a later op never overtakes an earlier one.
 */
export async function flushOutbox(handlers: Record<OutboxOp["type"], OpHandler>): Promise<void> {
  if (flushing) return;
  flushing = true;
  notify();
  try {
    // Requeue orphaned "sending" ops: a page death (iOS kill, reload) mid-
    // flush strands ops in "sending", which listPendingOps skips — they'd
    // never send again. At the START of a flush no op can genuinely be in
    // flight in this session (flushOutbox is single-flight), so any
    // "sending" op is an orphan. Cross-tab double-send is covered by
    // server-side idempotency (client_msg_id claim / idempotent PATCH).
    for (const op of await listOps()) {
      if (op.state === "sending") {
        await tx("readwrite", (s) => s.put({ ...op, state: "queued" }));
      }
    }
    const pending = await listPendingOps();
    for (const op of pending) {
      if (op.state === "failed") continue; // failed ops wait for an explicit retry
      const handler = handlers[op.type];
      if (!handler) continue;
      await markOpState(op.opId, "sending");
      let result: "delivered" | "retry" | "failed";
      try {
        result = await handler(op);
      } catch {
        result = "retry";
      }
      if (result === "delivered") {
        await removeOp(op.opId);
      } else if (result === "failed") {
        await markOpState(op.opId, "failed", op.lastError ?? "Rejected by server");
      } else {
        await markOpState(op.opId, "queued");
        break; // still unreachable — keep order, try again later
      }
    }
  } finally {
    flushing = false;
    notify();
  }
}

/** Explicit user retry of a failed op: requeue it so the next flush picks it up. */
export async function retryOp(opId: string): Promise<void> {
  await markOpState(opId, "queued");
}
