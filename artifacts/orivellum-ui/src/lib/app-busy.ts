/**
 * Global "unsafe to reload" registry (WP5 update-safety).
 *
 * Any surface with in-flight or unsaved work registers a busy reason here:
 * an unsent draft, a streaming reply, an upload, a running client operation.
 * The PWA update prompt reads this registry and refuses to reload while any
 * reason is held, so an update can never destroy work in progress.
 *
 * Usage:
 *   const release = acquireBusy('chat-stream');   // on start
 *   release();                                    // on finish (idempotent)
 *
 * Or for a boolean condition that flips on/off:
 *   setBusyFlag('chat-draft', draft.trim().length > 0);
 */

const reasons = new Map<string, number>(); // reason → hold count
const listeners = new Set<() => void>();

function notify() {
  for (const l of listeners) l();
}

/** Register a busy hold. Returns an idempotent release function. */
export function acquireBusy(reason: string): () => void {
  reasons.set(reason, (reasons.get(reason) ?? 0) + 1);
  notify();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const n = (reasons.get(reason) ?? 1) - 1;
    if (n <= 0) reasons.delete(reason);
    else reasons.set(reason, n);
    notify();
  };
}

/** Level-triggered variant: hold `reason` exactly while `active` is true. */
export function setBusyFlag(reason: string, active: boolean): void {
  const has = reasons.has(reason);
  if (active && !has) {
    reasons.set(reason, 1);
    notify();
  } else if (!active && has) {
    reasons.delete(reason);
    notify();
  }
}

export function isAppBusy(): boolean {
  return reasons.size > 0;
}

export function busyReasons(): string[] {
  return [...reasons.keys()];
}

export function subscribeBusy(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Human copy for the update prompt — why "Update now" is held back. */
export function busyLabel(): string | null {
  const r = busyReasons();
  if (r.length === 0) return null;
  if (r.some((x) => x.includes('draft'))) return 'You have an unsent draft';
  if (r.some((x) => x.includes('stream') || x.includes('send'))) return 'A reply is still streaming';
  if (r.some((x) => x.includes('upload'))) return 'An upload is in progress';
  return 'Work is in progress';
}
