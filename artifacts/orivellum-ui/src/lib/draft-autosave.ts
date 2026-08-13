/**
 * Debounced draft autosave with update-safety guarantees.
 *
 * Owns the full lifecycle of a "type → debounce → save (→ offline fallback)"
 * loop and keeps the app-busy registry honest across OVERLAPPING saves:
 * if save A is still on the network when the user types again (scheduling
 * save B), A's completion must NOT release the busy hold or discard B —
 * only the completion of the *latest* scheduled save (tracked by generation)
 * clears the pending state.
 *
 * `dispose()` (call on unmount) flushes the newest pending content
 * immediately — fire-and-forget with the offline fallback — so navigation
 * never drops the last debounce-window of typing, then releases the hold.
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
  /** Flush the newest pending save immediately and release the busy hold. */
  dispose: () => void;
}

export function createDraftAutosave<B>(opts: AutosaveOptions): DraftAutosave<B> {
  const delay = opts.delayMs ?? 1500;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let gen = 0; // increments per schedule; guards against overlapping saves
  let pending: AutosaveJob<B> | null = null;

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

  const run = async (myGen: number, job: AutosaveJob<B>): Promise<void> => {
    opts.onSavingChange?.(true);
    try {
      // Capture INSIDE the guarded block — it can throw if the editor was
      // destroyed mid-debounce; the finally below must still run.
      const body = job.capture();
      await persist(job, body);
    } catch { /* capture failed — dispose() already flushed what it could */ }
    finally {
      opts.onSavingChange?.(false);
      // Only the LATEST generation may clear the pending state. If the user
      // typed again while this save was on the network, a newer save owns
      // the busy hold now — releasing it here would let an update reload
      // (or unmount) drop that newer edit.
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
        void run(myGen, job);
      }, delay);
    },

    dispose(): void {
      if (timer) { clearTimeout(timer); timer = null; }
      const job = pending;
      pending = null;
      if (job) {
        // Flush the newest content now — fire-and-forget so unmount is not
        // blocked; the offline fallback still applies.
        try {
          const body = job.capture();
          void persist(job, body);
        } catch { /* editor already destroyed — nothing capturable remains */ }
      }
      setBusyFlag(opts.reason, false);
    },
  };
}
