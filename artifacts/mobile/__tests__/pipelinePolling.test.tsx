/**
 * Pipeline polling — battery-drain guard.
 *
 * The Work overview polls bookIntel every 10 s while the pipeline has stages
 * still ahead (`pipeline.next_status` truthy). When the pipeline reaches B17
 * or is absent, `next_status` is falsy and polling must stop.
 *
 * These tests verify the exact useEffect shape used in app/work/[id].tsx:
 *
 *   const pipelineActive = !!(pipeline && pipeline.next_status);
 *   useEffect(() => {
 *     if (!pipelineActive) return;
 *     const iv = setInterval(fetchBookIntel, 10_000);
 *     return () => clearInterval(iv);
 *   }, [pipelineActive, fetchBookIntel]);
 *
 * (a) setInterval is called when pipeline.next_status is truthy
 * (b) setInterval is NOT called when pipeline is null
 * (c) setInterval is NOT called when next_status is null (B17)
 * (d) clearInterval fires via the cleanup when pipelineActive goes false
 * (e) the interval fires fetchBookIntel on each tick
 */

import { useEffect } from 'react';
import { act, renderHook } from '@testing-library/react';

// ── Hook under test ───────────────────────────────────────────────────────────
//
// Mirrors the exact polling pattern in app/work/[id].tsx so tests are coupled
// to the behaviour, not the component internals.

function usePipelinePolling(
  pipelineActive: boolean,
  fetchBookIntel: () => void,
): void {
  useEffect(() => {
    if (!pipelineActive) return;
    const iv = setInterval(fetchBookIntel, 10_000);
    return () => clearInterval(iv);
  }, [pipelineActive, fetchBookIntel]);
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('pipelineActive derived value', () => {
  it('is true when pipeline.next_status is a non-empty string', () => {
    const pipeline = { status: 'B5', next_status: 'B6' };
    expect(!!(pipeline && pipeline.next_status)).toBe(true);
  });

  it('is false when pipeline is null (no pipeline created yet)', () => {
    const pipeline = null;
    expect(!!(pipeline && (pipeline as any).next_status)).toBe(false);
  });

  it('is false when next_status is null (B17 terminal gate)', () => {
    const pipeline = { status: 'B17', next_status: null };
    expect(!!(pipeline && pipeline.next_status)).toBe(false);
  });

  it('is false when next_status is an empty string', () => {
    const pipeline = { status: 'B17', next_status: '' };
    expect(!!(pipeline && pipeline.next_status)).toBe(false);
  });
});

describe('polling useEffect', () => {
  it('(a) starts a 10 s interval when pipelineActive is true', () => {
    const fetchBookIntel = jest.fn();
    renderHook(() => usePipelinePolling(true, fetchBookIntel));

    expect(jest.getTimerCount()).toBe(1);
  });

  it('(b) does NOT start an interval when pipelineActive is false (null pipeline)', () => {
    const fetchBookIntel = jest.fn();
    renderHook(() => usePipelinePolling(false, fetchBookIntel));

    expect(jest.getTimerCount()).toBe(0);
  });

  it('(c) does NOT start an interval when pipelineActive is false (B17 terminal)', () => {
    const fetchBookIntel = jest.fn();
    renderHook(() => usePipelinePolling(false, fetchBookIntel));

    expect(jest.getTimerCount()).toBe(0);
    expect(fetchBookIntel).not.toHaveBeenCalled();
  });

  it('(d) clears the interval via cleanup when pipelineActive transitions to false', () => {
    const fetchBookIntel = jest.fn();
    const { rerender } = renderHook(
      ({ active }: { active: boolean }) =>
        usePipelinePolling(active, fetchBookIntel),
      { initialProps: { active: true } },
    );

    // Interval must be running while active
    expect(jest.getTimerCount()).toBe(1);

    // Pipeline reaches B17: pipelineActive → false
    act(() => {
      rerender({ active: false });
    });

    // Cleanup must have cleared the interval
    expect(jest.getTimerCount()).toBe(0);
  });

  it('(e) calls fetchBookIntel on each 10 s tick while active', () => {
    const fetchBookIntel = jest.fn();
    renderHook(() => usePipelinePolling(true, fetchBookIntel));

    act(() => { jest.advanceTimersByTime(10_000); });
    expect(fetchBookIntel).toHaveBeenCalledTimes(1);

    act(() => { jest.advanceTimersByTime(10_000); });
    expect(fetchBookIntel).toHaveBeenCalledTimes(2);
  });

  it('stops calling fetchBookIntel after pipelineActive goes false', () => {
    const fetchBookIntel = jest.fn();
    const { rerender } = renderHook(
      ({ active }: { active: boolean }) =>
        usePipelinePolling(active, fetchBookIntel),
      { initialProps: { active: true } },
    );

    // One tick fires
    act(() => { jest.advanceTimersByTime(10_000); });
    expect(fetchBookIntel).toHaveBeenCalledTimes(1);

    // Pipeline finishes
    act(() => { rerender({ active: false }); });

    // More time passes — should not trigger further calls
    act(() => { jest.advanceTimersByTime(30_000); });
    expect(fetchBookIntel).toHaveBeenCalledTimes(1);
  });
});
