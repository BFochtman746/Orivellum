/**
 * Regression tests for the autosave controller's update-safety guarantees —
 * especially the OVERLAPPING-save race: save A completing on the network
 * while a newer save B is pending must not release the busy hold or drop B.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createDraftAutosave } from '../draft-autosave';
import { isAppBusy, busyReasons, setBusyFlag } from '../app-busy';

type Body = { text: string };

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const REASON = 'test-draft';

describe('createDraftAutosave', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    for (const r of busyReasons()) setBusyFlag(r, false);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('holds busy from schedule until the save lands', async () => {
    const saved: Body[] = [];
    const ctl = createDraftAutosave<Body>({ reason: REASON, delayMs: 100 });
    ctl.schedule({ capture: () => ({ text: 'a' }), save: async (b) => { saved.push(b); } });
    expect(isAppBusy()).toBe(true);
    await vi.advanceTimersByTimeAsync(100);
    expect(saved).toEqual([{ text: 'a' }]);
    expect(isAppBusy()).toBe(false);
  });

  it('SERIALIZATION: B never starts (or captures) while A is on the network — stale A cannot overwrite B', async () => {
    const a = deferred<void>();
    const saved: string[] = [];
    const ctl = createDraftAutosave<Body>({ reason: REASON, delayMs: 100 });

    // Save A fires and stalls on the network.
    ctl.schedule({ capture: () => ({ text: 'A' }), save: async (b) => { saved.push(b.text); await a.promise; } });
    await vi.advanceTimersByTimeAsync(100);
    expect(saved).toEqual(['A']);

    // User types again → save B scheduled while A is in flight.
    ctl.schedule({ capture: () => ({ text: 'B' }), save: async (b) => { saved.push(b.text); } });

    // B's debounce elapses while A is still in flight: B must NOT have
    // started — its capture+save wait for A, so writes reach the server in
    // order and A can never land after (and clobber) B.
    await vi.advanceTimersByTimeAsync(100);
    expect(saved).toEqual(['A']);
    expect(isAppBusy()).toBe(true);

    // A completes — the busy hold must survive (B is still pending), and
    // only then does B run.
    a.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(saved).toEqual(['A', 'B']);
    expect(isAppBusy()).toBe(false);
  });

  it('OVERLAP: A completing while B is still debouncing keeps busy held', async () => {
    const a = deferred<void>();
    const saved: string[] = [];
    const ctl = createDraftAutosave<Body>({ reason: REASON, delayMs: 100 });

    ctl.schedule({ capture: () => ({ text: 'A' }), save: async (b) => { saved.push(b.text); await a.promise; } });
    await vi.advanceTimersByTimeAsync(100);
    ctl.schedule({ capture: () => ({ text: 'B' }), save: async (b) => { saved.push(b.text); } });

    // A completes with B's debounce still ticking — hold must survive.
    a.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(isAppBusy()).toBe(true);

    await vi.advanceTimersByTimeAsync(100);
    expect(saved).toEqual(['A', 'B']);
    expect(isAppBusy()).toBe(false);
  });

  it('OVERLAP + dispose: unmount while A is in flight persists B, not nothing', async () => {
    const a = deferred<void>();
    const saved: string[] = [];
    const ctl = createDraftAutosave<Body>({ reason: REASON, delayMs: 100 });

    ctl.schedule({ capture: () => ({ text: 'A' }), save: async (b) => { saved.push(b.text); await a.promise; } });
    await vi.advanceTimersByTimeAsync(100);
    ctl.schedule({ capture: () => ({ text: 'B' }), save: async (b) => { saved.push(b.text); } });

    // Unmount while A is STILL in flight: busy must stay held (update reload
    // would abort A and the queued flush of B).
    ctl.dispose();
    await vi.advanceTimersByTimeAsync(0);
    expect(isAppBusy()).toBe(true);
    expect(saved).toEqual(['A']);

    // A settles → the flush of B runs → only then is the hold released.
    a.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(saved).toEqual(['A', 'B']);
    expect(isAppBusy()).toBe(false);
  });

  it('dispose holds busy until the flush is DURABLE, not merely dispatched', async () => {
    const flush = deferred<void>();
    const saved: Body[] = [];
    const ctl = createDraftAutosave<Body>({ reason: REASON, delayMs: 100 });
    ctl.schedule({
      capture: () => ({ text: 'draft' }),
      save: async (b) => { saved.push(b); await flush.promise; },
    });
    ctl.dispose();
    await vi.advanceTimersByTimeAsync(0);
    // The flush request is in flight — an update reload now would lose it,
    // so the busy hold must still be in place.
    expect(saved).toEqual([{ text: 'draft' }]);
    expect(isAppBusy()).toBe(true);

    flush.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(isAppBusy()).toBe(false);
    // The cancelled debounce timer must not fire a duplicate save.
    await vi.advanceTimersByTimeAsync(200);
    expect(saved).toHaveLength(1);
  });

  it('dispose holds busy until a failing flush reaches the outbox fallback', async () => {
    const outbox = deferred<void>();
    const fell: Body[] = [];
    const ctl = createDraftAutosave<Body>({
      reason: REASON, delayMs: 100, isNetworkError: () => true,
    });
    ctl.schedule({
      capture: () => ({ text: 'x' }),
      save: async () => { throw new Error('offline'); },
      fallback: async (b) => { fell.push(b); await outbox.promise; },
    });
    ctl.dispose();
    await vi.advanceTimersByTimeAsync(0);
    expect(fell).toEqual([{ text: 'x' }]);
    expect(isAppBusy()).toBe(true); // outbox write not yet durable

    outbox.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(isAppBusy()).toBe(false);
  });

  it('network failure routes to the fallback (outbox)', async () => {
    const fell: Body[] = [];
    const ctl = createDraftAutosave<Body>({
      reason: REASON,
      delayMs: 100,
      isNetworkError: () => true,
    });
    ctl.schedule({
      capture: () => ({ text: 'x' }),
      save: async () => { throw new Error('offline'); },
      fallback: async (b) => { fell.push(b); },
    });
    await vi.advanceTimersByTimeAsync(100);
    expect(fell).toEqual([{ text: 'x' }]);
    expect(isAppBusy()).toBe(false);
  });

  it('capture throwing (editor destroyed) still releases busy', async () => {
    const ctl = createDraftAutosave<Body>({ reason: REASON, delayMs: 100 });
    ctl.schedule({ capture: () => { throw new Error('destroyed'); }, save: async () => {} });
    await vi.advanceTimersByTimeAsync(100);
    expect(isAppBusy()).toBe(false);
    // dispose with a throwing capture must also be safe (release is async —
    // it happens once the flush chain settles).
    ctl.schedule({ capture: () => { throw new Error('destroyed'); }, save: async () => {} });
    ctl.dispose();
    await vi.advanceTimersByTimeAsync(0);
    expect(isAppBusy()).toBe(false);
  });

  it('reports saving state around the in-flight save', async () => {
    const states: boolean[] = [];
    const ctl = createDraftAutosave<Body>({
      reason: REASON,
      delayMs: 100,
      onSavingChange: (s) => states.push(s),
    });
    ctl.schedule({ capture: () => ({ text: 'a' }), save: async () => {} });
    expect(states).toEqual([]); // not saving during the debounce
    await vi.advanceTimersByTimeAsync(100);
    expect(states).toEqual([true, false]);
  });
});
