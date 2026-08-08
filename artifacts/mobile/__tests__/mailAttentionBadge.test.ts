/**
 * useMailAttentionCount — badge lifecycle tests.
 *
 * The hook has three guarantees:
 *
 *   (1) SUPPRESSION — returns 0 on any /mail* route regardless of server count
 *   (2) VISIBILITY  — returns the real server count on every non-mail route
 *   (3) FRESHNESS   — fires an immediate extra poll the moment the user LEAVES
 *                     /mail so the count reflects what they just did, not the
 *                     stale value from the previous 30 s tick
 *
 * Core badge scenario (task #1030):
 *   User visits /mail, marks all high-attention messages as handled.
 *   Server now reports high_attention = 0.
 *   User navigates away.
 *   Immediate re-poll fires and picks up the fresh 0.
 *   Badge correctly stays hidden.
 *
 * Contrast scenario:
 *   User visits /mail but leaves some messages unhandled.
 *   Server still reports high_attention = 3.
 *   User navigates away.
 *   Immediate re-poll fires and badge correctly shows 3.
 */

import { act, renderHook } from '@testing-library/react';
import { useMailAttentionCount } from '../hooks/useMailAttentionCount';

// ── Mocks ─────────────────────────────────────────────────────────────────────

// Mutable pathname — mutate to simulate navigation; beforeEach resets to '/'.
let mockPathname = '/';

jest.mock('expo-router', () => ({
  // Implementation reads mockPathname at call-time via closure.
  // beforeEach re-establishes this via mockImplementation so any
  // mockReturnValue set in a previous test never bleeds through.
  usePathname: jest.fn(() => mockPathname),
}));

// Per-test fetch behaviour factory; beforeEach resets to a safe default.
let mockFetchImpl: () => Promise<{ ok: boolean; json: () => Promise<unknown> }>;

jest.mock('../lib/api', () => ({
  mobileFetch: jest.fn((..._args: unknown[]) => mockFetchImpl()),
}));

// ── Helpers ───────────────────────────────────────────────────────────────────

function connectedResponse(highAttention: number) {
  return {
    ok: true,
    json: () => Promise.resolve({ connected: true, high_attention: highAttention }),
  };
}

const disconnectedResponse = {
  ok:   true,
  json: () => Promise.resolve({ connected: false, high_attention: 5 }),
};

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  jest.useFakeTimers();
  mockPathname = '/';
  mockFetchImpl = () => Promise.resolve(connectedResponse(0));

  // Re-establish the mockImplementation so that any mockReturnValue call from a
  // previous test never bleeds into this one.  mockImplementation always takes
  // precedence over a prior mockReturnValue.
  const { usePathname } = jest.requireMock('expo-router') as { usePathname: jest.Mock };
  usePathname.mockImplementation(() => mockPathname);
});

afterEach(() => {
  jest.useRealTimers();
  jest.clearAllMocks();
});

// ── 1. Badge suppression on /mail* routes ─────────────────────────────────────

describe('badge suppression while on mail route', () => {
  it('returns 0 on /mail even when server reports high_attention = 3', async () => {
    mockPathname = '/mail';
    mockFetchImpl = () => Promise.resolve(connectedResponse(3));

    const { result } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    expect(result.current).toBe(0);
  });

  it('returns 0 on /mail/<id>  (message detail)', async () => {
    mockPathname = '/mail/msg-abc-123';
    mockFetchImpl = () => Promise.resolve(connectedResponse(5));

    const { result } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    expect(result.current).toBe(0);
  });

  it('returns 0 on /mail/settings', async () => {
    mockPathname = '/mail/settings';
    mockFetchImpl = () => Promise.resolve(connectedResponse(2));

    const { result } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    expect(result.current).toBe(0);
  });

  it('returns 0 on /mail/connect', async () => {
    mockPathname = '/mail/connect';
    mockFetchImpl = () => Promise.resolve(connectedResponse(1));

    const { result } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    expect(result.current).toBe(0);
  });

  it('returns 0 on /mail/compose/<id>', async () => {
    mockPathname = '/mail/compose/action-req-xyz';
    mockFetchImpl = () => Promise.resolve(connectedResponse(4));

    const { result } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    expect(result.current).toBe(0);
  });
});

// ── 2. Real count on non-mail routes ─────────────────────────────────────────

describe('real count returned on non-mail routes', () => {
  it('returns high_attention when connected and off mail route', async () => {
    mockPathname = '/';
    mockFetchImpl = () => Promise.resolve(connectedResponse(3));

    const { result } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    expect(result.current).toBe(3);
  });

  it('returns 0 when connected=false regardless of high_attention value', async () => {
    mockPathname = '/conversations';
    mockFetchImpl = () => Promise.resolve(disconnectedResponse);

    const { result } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    expect(result.current).toBe(0);
  });

  it('returns 0 on initial mount before first poll resolves', () => {
    mockPathname = '/';
    const { result } = renderHook(() => useMailAttentionCount());
    // Do NOT flush the promise — check synchronously.
    expect(result.current).toBe(0);
  });
});

// ── 3. Re-poll fires immediately when leaving /mail ───────────────────────────

describe('immediate re-poll on leaving mail route', () => {
  it('fires an extra poll when navigating from /mail to /', async () => {
    mockPathname = '/mail';
    mockFetchImpl = () => Promise.resolve(connectedResponse(3));

    const { rerender } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    const callsOnMail = (require('../lib/api').mobileFetch as jest.Mock).mock.calls.length;

    // Simulate navigating away — mutate then re-render.
    mockPathname = '/';
    await act(async () => {
      rerender();
      await Promise.resolve();
    });

    const callsAfterLeaving = (require('../lib/api').mobileFetch as jest.Mock).mock.calls.length;

    // At least one extra call must have fired immediately upon leaving /mail.
    expect(callsAfterLeaving).toBeGreaterThan(callsOnMail);
  });

  /**
   * Core scenario (task #1030): user handles all mail → badge must stay hidden.
   *
   * Timeline:
   *   1. User is on /mail. Server reports 3 items.
   *   2. User reads and defers all items. Server now reports 0.
   *   3. mockFetchImpl updated to return 0 (simulates server state post-action).
   *   4. User navigates to /. Immediate re-poll fires.
   *   5. Hook receives fresh 0 → badge stays hidden.
   */
  it('badge stays 0 after user handles all high-attention messages', async () => {
    mockPathname = '/mail';
    mockFetchImpl = () => Promise.resolve(connectedResponse(3));

    const { result, rerender } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    // Badge is suppressed on /mail regardless of server count.
    expect(result.current).toBe(0);

    // User finishes handling all messages — server now returns 0.
    mockFetchImpl = () => Promise.resolve(connectedResponse(0));

    // Navigate away → immediate re-poll fires with the updated server count.
    mockPathname = '/';
    await act(async () => {
      rerender();
      await Promise.resolve();
      await Promise.resolve();
    });

    // Fresh poll returned 0 → badge must remain hidden.
    expect(result.current).toBe(0);
  });

  /**
   * Contrast scenario: user leaves without handling messages → badge appears.
   *
   * Timeline:
   *   1. User visits /mail but does not act on messages.
   *   2. Server still reports high_attention = 3.
   *   3. User navigates to /. Immediate re-poll fires.
   *   4. Hook receives 3 → badge shows.
   */
  it('badge re-appears when the user leaves without handling messages', async () => {
    mockPathname = '/mail';
    mockFetchImpl = () => Promise.resolve(connectedResponse(3));

    const { result, rerender } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    // Badge suppressed on /mail.
    expect(result.current).toBe(0);

    // Server count unchanged — user did not handle anything.
    // Navigate away → immediate re-poll fires and returns the real count.
    mockPathname = '/';
    await act(async () => {
      rerender();
      await Promise.resolve();
      await Promise.resolve();
    });

    // Stale 0 must NOT persist — badge must now show 3.
    expect(result.current).toBe(3);
  });

  it('does NOT fire an extra poll when navigating between two non-mail routes', async () => {
    mockPathname = '/';
    const { rerender } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    const callsAtRoot = (require('../lib/api').mobileFetch as jest.Mock).mock.calls.length;

    // Navigate to /conversations — prevOnMailRef stays false, no re-poll.
    mockPathname = '/conversations';
    await act(async () => {
      rerender();
      await Promise.resolve();
    });

    const callsAfter = (require('../lib/api').mobileFetch as jest.Mock).mock.calls.length;
    expect(callsAfter).toBe(callsAtRoot);
  });

  it('does NOT fire an extra poll when navigating between /mail sub-routes', async () => {
    mockPathname = '/mail';
    const { rerender } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    const callsAtMail = (require('../lib/api').mobileFetch as jest.Mock).mock.calls.length;

    // Navigate within mail (e.g. open a message detail) — still a /mail route.
    mockPathname = '/mail/msg-abc';
    await act(async () => {
      rerender();
      await Promise.resolve();
    });

    const callsAfter = (require('../lib/api').mobileFetch as jest.Mock).mock.calls.length;
    // prevOnMailRef stays true → no extra poll.
    expect(callsAfter).toBe(callsAtMail);
  });
});

// ── 4. Regular 30 s interval ──────────────────────────────────────────────────

describe('30 s polling interval', () => {
  it('fires poll on mount (t=0)', async () => {
    mockPathname = '/';
    const mobileFetch = (require('../lib/api').mobileFetch as jest.Mock);

    renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    expect(mobileFetch).toHaveBeenCalledTimes(1);
  });

  it('fires a second poll after 30 s', async () => {
    mockPathname = '/';
    mockFetchImpl = () => Promise.resolve(connectedResponse(2));
    const mobileFetch = (require('../lib/api').mobileFetch as jest.Mock);

    renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      jest.advanceTimersByTime(30_000);
      await Promise.resolve();
    });

    expect(mobileFetch).toHaveBeenCalledTimes(2);
  });

  it('clears the interval on unmount (no calls after unmount)', async () => {
    mockPathname = '/';
    const mobileFetch = (require('../lib/api').mobileFetch as jest.Mock);

    const { unmount } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });

    unmount();

    await act(async () => {
      jest.advanceTimersByTime(30_000);
      await Promise.resolve();
    });

    // Only the initial mount poll should have fired.
    expect(mobileFetch).toHaveBeenCalledTimes(1);
  });
});

// ── 5. Silent failure on network error ───────────────────────────────────────

describe('silent failure on network error', () => {
  it('keeps the previous count when the poll throws', async () => {
    mockPathname = '/';
    mockFetchImpl = () => Promise.resolve(connectedResponse(4));

    const { result } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });
    expect(result.current).toBe(4);

    // Next poll throws — silently swallowed.
    mockFetchImpl = () => Promise.reject(new Error('network error'));

    await act(async () => {
      jest.advanceTimersByTime(30_000);
      await Promise.resolve();
    });

    // Count must stay at last known value (4), not reset to 0.
    expect(result.current).toBe(4);
  });

  it('keeps the previous count when the poll returns a non-ok response', async () => {
    mockPathname = '/';
    mockFetchImpl = () => Promise.resolve(connectedResponse(2));

    const { result } = renderHook(() => useMailAttentionCount());
    await act(async () => { await Promise.resolve(); });
    expect(result.current).toBe(2);

    // Next poll returns HTTP error — silently ignored.
    mockFetchImpl = () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) });

    await act(async () => {
      jest.advanceTimersByTime(30_000);
      await Promise.resolve();
    });

    // Non-ok response leaves the count unchanged.
    expect(result.current).toBe(2);
  });
});
