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

// Stores the config object passed to PanResponder.create so tests can invoke
// the capture handler and scroll-guard logic directly.
let mockPanCreateArg: any = null;

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
    // PanResponder is created once in useRef.  Capture the config so tests
    // can invoke onMoveShouldSetPanResponderCapture and the other handlers
    // directly to verify the scroll-guard logic.
    PanResponder: {
      create: jest.fn((config: any) => {
        mockPanCreateArg = config;
        return { panHandlers: {} };
      }),
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

// ── Scroll-guard and scrollHandler ────────────────────────────────────────────
//
// These tests exercise the onMoveShouldSetPanResponderCapture + scrollHandler
// contract:
//   • scrollHandler updates the internal scrollY ref
//   • the capture handler returns true  only when scrollY ≤ 1 AND the gesture
//     is downward-dominant (allows dismiss at the top)
//   • the capture handler returns false when scrollY > 1 (list scrolled,
//     ScrollView must keep the responder so it can scroll normally)
//   • scrollY is reset to 0 on every sheet open
//
// The tests invoke PanResponder handlers via mockPanCreateArg — the config
// object captured inside the PanResponder.create mock — to avoid spinning up
// a full gesture simulation.

describe('scroll-guard and scrollHandler', () => {
  /** Simulate a gesture-state object with the given dy/dx (all other fields 0). */
  function gesture(dy: number, dx = 0): [any, any] {
    return [{}, { dy, dx, vy: 0, vx: 0, x0: 0, y0: 0, moveX: 0, moveY: 0 }];
  }
  /** Simulate an onScroll event at the given vertical offset. */
  function scrollEvent(y: number) {
    return { nativeEvent: { contentOffset: { y } } };
  }

  it('scrollHandler is returned as a stable function (same reference across renders)', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: false } },
    );
    const first = result.current.scrollHandler;
    act(() => { rerender({ v: true }); });
    expect(result.current.scrollHandler).toBe(first);
    expect(typeof first).toBe('function');
  });

  it('capture handler allows dismiss (true) when scrollY is 0 and drag is downward', () => {
    renderHook(() => useSheetAnimation(true, 400));
    const capture = mockPanCreateArg?.onMoveShouldSetPanResponderCapture;
    expect(capture?.(...gesture(20, 2))).toBe(true);
  });

  it('capture handler blocks dismiss (false) when list is scrolled down (scrollY > 1)', () => {
    const { result } = renderHook(() => useSheetAnimation(true, 400));
    act(() => { result.current.scrollHandler(scrollEvent(50)); });
    const capture = mockPanCreateArg?.onMoveShouldSetPanResponderCapture;
    expect(capture?.(...gesture(20, 2))).toBe(false);
  });

  it('capture handler allows dismiss again after user scrolls back to the top', () => {
    const { result } = renderHook(() => useSheetAnimation(true, 400));
    const capture = mockPanCreateArg?.onMoveShouldSetPanResponderCapture;

    act(() => { result.current.scrollHandler(scrollEvent(80)); });
    expect(capture?.(...gesture(20, 2))).toBe(false); // mid-list — blocked

    act(() => { result.current.scrollHandler(scrollEvent(0)); });
    expect(capture?.(...gesture(20, 2))).toBe(true);  // back at top — allowed
  });

  it('scrollY resets to 0 on sheet reopen so dismiss is always available fresh', () => {
    const { result, rerender } = renderHook(
      ({ v }: { v: boolean }) => useSheetAnimation(v, 400),
      { initialProps: { v: true } },
    );
    // Scroll to mid-list then close
    act(() => { result.current.scrollHandler(scrollEvent(120)); });
    const capture = mockPanCreateArg?.onMoveShouldSetPanResponderCapture;
    expect(capture?.(...gesture(20, 2))).toBe(false); // guard active

    // Close → reopen: scrollY must reset
    act(() => { rerender({ v: false }); });
    act(() => { rerender({ v: true }); });
    expect(capture?.(...gesture(20, 2))).toBe(true); // reset — dismiss works immediately
  });

  it('capture handler yields to horizontal gestures even when at the top of scroll', () => {
    renderHook(() => useSheetAnimation(true, 400));
    const capture = mockPanCreateArg?.onMoveShouldSetPanResponderCapture;
    // dx dominates → horizontal swipe, should not be captured
    expect(capture?.(...gesture(5, 20))).toBe(false);
  });

  it('capture handler does not fire for tiny drags (dy ≤ 8 px)', () => {
    renderHook(() => useSheetAnimation(true, 400));
    const capture = mockPanCreateArg?.onMoveShouldSetPanResponderCapture;
    expect(capture?.(...gesture(7, 0))).toBe(false);
  });

  it('capture handler sub-pixel tolerance: scrollY === 1 still allows dismiss', () => {
    const { result } = renderHook(() => useSheetAnimation(true, 400));
    // Exactly 1 px — within the ≤1 tolerance for sub-pixel float imprecision
    act(() => { result.current.scrollHandler(scrollEvent(1)); });
    const capture = mockPanCreateArg?.onMoveShouldSetPanResponderCapture;
    expect(capture?.(...gesture(20, 2))).toBe(true);
  });

  it('capture handler: scrollY === 2 blocks dismiss (above tolerance)', () => {
    const { result } = renderHook(() => useSheetAnimation(true, 400));
    act(() => { result.current.scrollHandler(scrollEvent(2)); });
    const capture = mockPanCreateArg?.onMoveShouldSetPanResponderCapture;
    expect(capture?.(...gesture(20, 2))).toBe(false);
  });
});
