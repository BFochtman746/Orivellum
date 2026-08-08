/**
 * useSheetAnimation — animation lifecycle and race-condition tests.
 *
 * Verifies that rapid open/close transitions never leave a sheet stuck
 * on screen or invisible when it should be visible.
 *
 *   (1) BASIC       — rendered=true on open; rendered=false after close completes
 *   (2) RACE A      — close → re-open before exit finishes: rendered stays true;
 *                     the exitGen counter suppresses the stale callback
 *   (3) RACE B      — open → close → open (multiple rapid cycles): final state
 *                     always matches the last visible value
 *   (4) INTERRUPTED — exit callback with finished=false never unmounts the sheet
 *   (5) PARAMS      — spring called with toValue=0; timing exit uses sheetHeight+60
 */

import { act, renderHook } from '@testing-library/react';
import { useSheetAnimation } from '../lib/useSheetAnimation';

// ── Animated mock ─────────────────────────────────────────────────────────────
//
// Variable names referenced inside jest.mock() factories MUST start with "mock"
// so babel-jest can hoist them correctly alongside the jest.mock() call.
//
// The hook calls:
//   Animated.parallel([spring, timing]).start()          — open (no callback)
//   Animated.parallel([timing, timing]).start(exitCb)   — close (has callback)
//
// Only the close parallel receives a callback; we capture those in
// mockExitCallbacks so each test can fire them manually.

const mockExitCallbacks: Array<(r: { finished: boolean }) => void> = [];

jest.mock('react-native', () => {
  // Minimal stand-in for Animated.Value — holds an initial value but the
  // animations themselves are mocked and don't actually move it.
  class MockAnimatedValue {
    _v: number;
    constructor(v: number) { this._v = v; }
    setValue(v: number) { this._v = v; }
  }

  return {
    Animated: {
      Value: MockAnimatedValue,
      // Open path uses spring — track call args for param assertions.
      spring: jest.fn(() => ({ start: jest.fn() })),
      // Close path uses timing — track call args for param assertions.
      timing: jest.fn(() => ({ start: jest.fn() })),
      // parallel wraps the animations; only the CLOSE parallel includes a
      // completion callback.  Capture it for manual control in tests.
      parallel: jest.fn(() => ({
        start: jest.fn((cb?: (r: { finished: boolean }) => void) => {
          if (cb) mockExitCallbacks.push(cb);
        }),
      })),
    },
  };
});

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Fire the most recently captured exit callback (LIFO — latest close first). */
function resolveLatestExit(finished = true) {
  const cb = mockExitCallbacks.pop();
  act(() => { cb?.({ finished }); });
}

/**
 * Fire ALL captured exit callbacks in order (FIFO — oldest first).
 * Useful for "flush everything and check the final state" scenarios.
 */
function resolveAllExits(finished = true) {
  const cbs = mockExitCallbacks.splice(0);
  act(() => { cbs.forEach(cb => cb({ finished })); });
}

beforeEach(() => {
  mockExitCallbacks.length = 0;
  jest.clearAllMocks();
});

// ── 1. Basic open/close lifecycle ─────────────────────────────────────────────

describe('basic open/close lifecycle', () => {
  it('rendered is false on initial mount when visible=false', () => {
    const { result } = renderHook(() => useSheetAnimation(false, 400));
    expect(result.current.rendered).toBe(false);
  });

  it('rendered is true on initial mount when visible=true', () => {
    const { result } = renderHook(() => useSheetAnimation(true, 400));
    expect(result.current.rendered).toBe(true);
  });

  it('rendered becomes true when visible transitions false → true', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: false } },
    );
    act(() => { rerender({ v: true }); });
    expect(result.current.rendered).toBe(true);
  });

  it('rendered stays true while exit animation is in progress (before callback fires)', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );
    act(() => { rerender({ v: false }); });
    // Callback not yet fired — sheet must remain mounted so the animation is visible
    expect(result.current.rendered).toBe(true);
  });

  it('rendered becomes false after exit animation completes (finished=true)', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );
    act(() => { rerender({ v: false }); });
    resolveLatestExit(true);
    expect(result.current.rendered).toBe(false);
  });

  it('opening a second time while already open does not change rendered', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );
    expect(result.current.rendered).toBe(true);
    // Visible stays true — effect won't re-run since the dep hasn't changed.
    act(() => { rerender({ v: true }); });
    expect(result.current.rendered).toBe(true);
  });
});

// ── 2. Race A: close then immediately re-open ─────────────────────────────────

describe('race A: close → re-open before exit animation finishes', () => {
  it('rendered stays true when re-opened before exit callback fires', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );

    // Close — exit callback captured but NOT yet fired
    act(() => { rerender({ v: false }); });
    expect(result.current.rendered).toBe(true);

    // Re-open — exitGen is bumped, invalidating the in-flight close
    act(() => { rerender({ v: true }); });
    expect(result.current.rendered).toBe(true);

    // Fire the now-stale exit callback with finished=true.
    // exitGen guard must block setRendered(false).
    resolveLatestExit(true);
    expect(result.current.rendered).toBe(true);
  });

  it('rendered never flickers to false during rapid close → re-open', () => {
    const snapshots: boolean[] = [];
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );
    snapshots.push(result.current.rendered); // true

    act(() => { rerender({ v: false }); });
    snapshots.push(result.current.rendered); // true (animation in progress)

    act(() => { rerender({ v: true }); });
    snapshots.push(result.current.rendered); // true

    resolveLatestExit(true); // stale callback — no-op
    snapshots.push(result.current.rendered); // true

    expect(snapshots).toEqual([true, true, true, true]);
  });

  it('a subsequent close after re-open completes correctly', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );

    // Rapid: close → re-open → close
    act(() => { rerender({ v: false }); }); // exit cb #1 captured
    act(() => { rerender({ v: true }); });  // exitGen bumped
    act(() => { rerender({ v: false }); }); // exit cb #2 captured

    // cb #1 is stale — must not unmount
    const staleCb = mockExitCallbacks[0];
    act(() => { staleCb?.({ finished: true }); });
    expect(result.current.rendered).toBe(true);

    // cb #2 is the live one — must unmount
    resolveLatestExit(true);
    expect(result.current.rendered).toBe(false);
  });
});

// ── 3. Race B: multiple rapid open/close cycles ───────────────────────────────

describe('race B: multiple rapid open/close toggles', () => {
  it('rendered ends true when visible=true is the final state', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );
    act(() => { rerender({ v: false }); });
    act(() => { rerender({ v: true }); });
    act(() => { rerender({ v: false }); });
    act(() => { rerender({ v: true }); });

    // All captured exit callbacks are stale — none must unmount the sheet
    resolveAllExits(true);
    expect(result.current.rendered).toBe(true);
  });

  it('rendered ends false when visible=false is the final state', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );
    act(() => { rerender({ v: false }); });
    act(() => { rerender({ v: true }); });
    act(() => { rerender({ v: false }); });

    // Stale callbacks are no-ops; the final live callback unmounts
    resolveAllExits(true);
    expect(result.current.rendered).toBe(false);
  });

  it('exitGen increments monotonically — each stale callback is rejected', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );

    // Cycle 1
    act(() => { rerender({ v: false }); });
    const cb1 = mockExitCallbacks[mockExitCallbacks.length - 1];
    act(() => { rerender({ v: true }); });

    // Cycle 2
    act(() => { rerender({ v: false }); });
    const cb2 = mockExitCallbacks[mockExitCallbacks.length - 1];
    act(() => { rerender({ v: true }); });

    // Both cb1 and cb2 are now stale — neither must unmount
    act(() => {
      cb1?.({ finished: true });
      cb2?.({ finished: true });
    });
    expect(result.current.rendered).toBe(true);
  });
});

// ── 4. Interrupted exit animation (finished=false) ────────────────────────────

describe('interrupted exit animation', () => {
  it('rendered stays true when exit fires with finished=false', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );
    act(() => { rerender({ v: false }); });
    resolveLatestExit(false); // finished=false — animation was cancelled
    // A non-completed exit must never unmount; the sheet is in an unknown state
    expect(result.current.rendered).toBe(true);
  });

  it('a subsequent full close after an interrupted one unmounts correctly', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );

    // First close attempt — interrupted mid-animation
    act(() => { rerender({ v: false }); });
    resolveLatestExit(false);
    expect(result.current.rendered).toBe(true);

    // Re-open then close again — this exit completes normally
    act(() => { rerender({ v: true }); });
    act(() => { rerender({ v: false }); });
    resolveLatestExit(true);
    expect(result.current.rendered).toBe(false);
  });

  it('stale interrupted callback + valid close: only valid close unmounts', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );

    // Close → re-open (making first exit stale) → close
    act(() => { rerender({ v: false }); });
    act(() => { rerender({ v: true }); });
    act(() => { rerender({ v: false }); });

    // Fire the stale callback with finished=false (double guard: stale + incomplete)
    const staleCb = mockExitCallbacks[0];
    act(() => { staleCb?.({ finished: false }); });
    expect(result.current.rendered).toBe(true);

    // Fire the live callback with finished=true
    resolveLatestExit(true);
    expect(result.current.rendered).toBe(false);
  });
});

// ── 5. Animation parameters ───────────────────────────────────────────────────

describe('animation parameters', () => {
  it('spring is called with toValue=0 and useNativeDriver=true on open', () => {
    const { Animated } = jest.requireMock('react-native') as {
      Animated: { spring: jest.Mock };
    };
    const { rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: false } },
    );
    act(() => { rerender({ v: true }); });
    expect(Animated.spring).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ toValue: 0, useNativeDriver: true }),
    );
  });

  it('spring uses tension=85 and friction=13 (project standard)', () => {
    const { Animated } = jest.requireMock('react-native') as {
      Animated: { spring: jest.Mock };
    };
    const { rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: false } },
    );
    act(() => { rerender({ v: true }); });
    expect(Animated.spring).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ tension: 85, friction: 13 }),
    );
  });

  it('exit timing uses toValue=sheetHeight+60 so the sheet slides fully off-screen', () => {
    const { Animated } = jest.requireMock('react-native') as {
      Animated: { timing: jest.Mock };
    };
    const SHEET_H = 520;
    const { rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, SHEET_H),
      { initialProps: { v: true } },
    );
    act(() => { rerender({ v: false }); });
    expect(Animated.timing).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ toValue: SHEET_H + 60, useNativeDriver: true }),
    );
  });

  it('re-opening after a close calls spring again (always starts from off-screen)', () => {
    const { Animated } = jest.requireMock('react-native') as {
      Animated: { spring: jest.Mock };
    };
    const { rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: false } },
    );

    act(() => { rerender({ v: true }); });
    const callsAfterFirstOpen = Animated.spring.mock.calls.length;
    expect(callsAfterFirstOpen).toBeGreaterThan(0);

    act(() => { rerender({ v: false }); });
    resolveLatestExit(true);

    act(() => { rerender({ v: true }); });
    expect(Animated.spring.mock.calls.length).toBeGreaterThan(callsAfterFirstOpen);
    const lastArgs = Animated.spring.mock.calls.at(-1)!;
    expect(lastArgs[1]).toMatchObject({ toValue: 0 });
  });
});
