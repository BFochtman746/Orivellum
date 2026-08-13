/**
 * Debounced draft autosave with update-safety guarantees.
 *
 * Owns the full lifecycle of a "type → debounce → save (→ offline fallback)"
 * loop with three hard rules:
 *
 * 1. SERIALIZED WRITES — saves run strictly one at a time through an internal
 *    promise chain, and each save captures content when it STARTS (not when
 *    scheduled). A newer save can therefore never be overwritten by an older
 *    in-flight one reaching the server late.
 * 2. HONEST BUSY HOLD — the app-busy reason is held from the first schedule
 *    until the latest scheduled save has durably landed (server or outbox).
 *    An older save completing cannot release the hold while a newer one is
 *    pending (generation-tracked).
 * 3. DURABLE FLUSH — `dispose()` (call on unmount) flushes the newest pending
 *    content and keeps the busy hold until that flush AND any in-flight save
 *    have settled. Dispatching an async request is not persistence; a PWA
 *    update reload is blocked until the write is actually durable.
 */
import { setBusyFlag } from './app-busy';

export interface AutosaveJob<B> {
  /** Capture the current content. May throw (e.g. editor destroyed) — the
   *  controller treats a throw as "nothing to save". */
  capture: () => B;
  /** Persist the captured content (network). Throws on failure. */
  save: (body: B) => Promise<void>;
  /** Durable fallback (outbox) used when `save` fails with a network error. */
  fallback?: (body: B) => Promise<void>;
}

export interface AutosaveOptions {
  /** Busy-registry reason key held while a save is pending or in flight. */
  reason: string;
  /** Debounce delay in ms (default 1500). */
  delayMs?: number;
  /** Classifies save errors that should route to the fallback. */
  isNetworkError?: (err: unknown) => boolean;
  /** Saving-indicator callback (spinner state). */
  onSavingChange?: (saving: boolean) => void;
}

export interface DraftAutosave<B> {
  /** (Re)schedule a save after the debounce delay. Replaces any pending one. */
  schedule: (job: AutosaveJob<B>) => void;
  /** Flush the newest pending save and release the busy hold once durable. */
  dispose: () => void;
}

export function createDraftAutosave<B>(opts: AutosaveOptions): DraftAutosave<B> {
  const delay = opts.delayMs ?? 1500;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let gen = 0; // increments per schedule; guards against overlapping saves
  let pending: AutosaveJob<B> | null = null;
  // All persistence is serialized through this chain — rule 1.
  let chain: Promise<void> = Promise.resolve();

  const persist = async (job: AutosaveJob<B>, body: B): Promise<void> => {
    try {
      await job.save(body);
    } catch (err) {
      if (job.fallback && opts.isNetworkError?.(err)) {
        try { await job.fallback(body); } catch { /* IDB unavailable — next edit retries */ }
      }
      /* otherwise silent — the next scheduled save retries */
    }
  };

  const runSerialized = async (myGen: number, job: AutosaveJob<B>): Promise<void> => {
    opts.onSavingChange?.(true);
    try {
      // Capture INSIDE the guarded block and only once any previous save has
      // fully finished — the freshest content always wins, and a throw
      // (editor destroyed mid-debounce) still reaches the finally.
      const body = job.capture();
      await persist(job, body);
    } catch { /* capture failed — dispose() already flushed what it could */ }
    finally {
      opts.onSavingChange?.(false);
      // Only the LATEST generation may clear the pending state — rule 2.
      if (gen === myGen) {
        pending = null;
        setBusyFlag(opts.reason, false);
      }
    }
  };

  return {
    schedule(job: AutosaveJob<B>): void {
      if (timer) clearTimeout(timer);
      const myGen = ++gen;
      pending = job;
      setBusyFlag(opts.reason, true);
      timer = setTimeout(() => {
        timer = null;
        chain = chain.then(() => runSerialized(myGen, job));
      }, delay);
    },

    dispose(): void {
      // Invalidate every generation: no in-flight save may release the busy
      // hold anymore — only the durable-flush completion below does.
      gen++;
      if (timer) { clearTimeout(timer); timer = null; }
      const job = pending;
      pending = null;
      if (job) {
        chain = chain.then(async () => {
          try {
            const body = job.capture();
            await persist(job, body);
          } catch { /* editor already destroyed — nothing capturable remains */ }
        });
      }
      // Rule 3: release only after the flush and any in-flight save settled.
      void chain.finally(() => setBusyFlag(opts.reason, false));
    },
  };
}
