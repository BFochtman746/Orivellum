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

  it('OVERLAP: save A completing while B is pending keeps busy held and B still saves', async () => {
    const a = deferred<void>();
    const saved: string[] = [];
    const ctl = createDraftAutosave<Body>({ reason: REASON, delayMs: 100 });

    // Save A fires and stalls on the network.
    ctl.schedule({ capture: () => ({ text: 'A' }), save: async (b) => { saved.push(b.text); await a.promise; } });
    await vi.advanceTimersByTimeAsync(100);
    expect(saved).toEqual(['A']);

    // User types again → save B scheduled while A is in flight.
    ctl.schedule({ capture: () => ({ text: 'B' }), save: async (b) => { saved.push(b.text); } });

    // A completes — the busy hold must survive (B is still pending).
    a.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(isAppBusy()).toBe(true);

    // B fires and completes — only now is the hold released.
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

    a.resolve();
    await vi.advanceTimersByTimeAsync(0);

    // Unmount before B's debounce fires: dispose must flush B immediately.
    ctl.dispose();
    await vi.advanceTimersByTimeAsync(0);
    expect(saved).toEqual(['A', 'B']);
    expect(isAppBusy()).toBe(false);
  });

  it('dispose flushes the pending save and releases busy', async () => {
    const saved: Body[] = [];
    const ctl = createDraftAutosave<Body>({ reason: REASON, delayMs: 100 });
    ctl.schedule({ capture: () => ({ text: 'draft' }), save: async (b) => { saved.push(b); } });
    ctl.dispose();
    await vi.advanceTimersByTimeAsync(0);
    expect(saved).toEqual([{ text: 'draft' }]);
    expect(isAppBusy()).toBe(false);
    // The cancelled debounce timer must not fire a duplicate save.
    await vi.advanceTimersByTimeAsync(200);
    expect(saved).toHaveLength(1);
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
    // dispose with a throwing capture must also be safe.
    ctl.schedule({ capture: () => { throw new Error('destroyed'); }, save: async () => {} });
    ctl.dispose();
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
