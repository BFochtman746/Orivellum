/**
 * TtsContext — applySettings() staleness guard.
 *
 * Mirrors the logic in e2e/tests/06-tts-voice-stale.spec.ts test 4.
 *
 * The mobile staleness guard works through sessionCounterRef inside
 * TtsContext.tsx: every call to play(), applySettings(), or skipTo() bumps the
 * counter.  synthesizePart() checks sessionCounterRef.current === sessionId
 * after each await; when they diverge it throws 'tts-stale' which playPartAt()
 * silently swallows, preventing any createAudioPlayer / player.play() call for
 * the stale session.
 *
 * Key scenario (test 4 equivalent):
 *   voice A playing  →  applySettings(B) [synthesis in-flight]
 *                    →  applySettings(C) immediately [synthesis in-flight]
 *   → B's fetch resolves late  → stale check fires  → B discarded (no player)
 *   → C's fetch resolves       → check passes        → player created with C
 *
 * Additional tests cover simpler applySettings paths and the no-op idle guard.
 */

import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { createAudioPlayer } from 'expo-audio';
import { TtsProvider, useTts } from '../context/TtsContext';

// ── Deferred-promise helpers ──────────────────────────────────────────────────

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: Error) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

// Convenience: a resolve function that fills in a fetch-response shape
type FetchResponse = { ok: boolean; json: () => Promise<{ path: string }> };

function makeFetchResponse(path: string): FetchResponse {
  return { ok: true, json: () => Promise.resolve({ path }) };
}

// ── Mock: mobileFetch ─────────────────────────────────────────────────────────
//
// Each test populates fetchQueue before triggering synthesis.  mobileFetch
// shifts the next deferred off the queue; tests resolve them in controlled
// order to simulate in-flight, late-resolving synthesis requests.

const fetchQueue: Array<Deferred<FetchResponse>> = [];

jest.mock('../lib/api', () => ({
  mobileFetch: jest.fn(() => {
    const d = fetchQueue.shift();
    if (!d) {
      // Fallback: immediate success (inlined — jest.mock factories cannot reference
      // out-of-scope helpers like makeFetchResponse)
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ path: '/fallback.mp3' }) });
    }
    return d.promise;
  }),
}));

// ── Mock: expo-audio ──────────────────────────────────────────────────────────
//
// createAudioPlayer is tracked so tests can assert which URI was used.

const mockPlay   = jest.fn();
const mockPause  = jest.fn();
const mockRemove = jest.fn();

jest.mock('expo-audio', () => ({
  createAudioPlayer: jest.fn((opts: { uri: string }) => ({
    play:   mockPlay,
    pause:  mockPause,
    remove: mockRemove,
    addListener: jest.fn(() => ({ remove: jest.fn() })),
  })),
  setAudioModeAsync: jest.fn(() => Promise.resolve()),
}));

// ── Mock: helpers ─────────────────────────────────────────────────────────────

jest.mock('../lib/token', () => ({ getApiToken: jest.fn(() => 'test-token') }));
jest.mock('react-native', () => ({ Alert: { alert: jest.fn() } }));

// ── Fixtures ──────────────────────────────────────────────────────────────────

const PARTS     = ['Test text only one part.'];
const DOC_ID    = 'doc-stale-001';
const DOC_TITLE = 'Stale Test Document';
const SPEED     = 1.0;
const VOICE_A   = 'af_heart';
const VOICE_B   = 'af_bella';
const VOICE_C   = 'af_nova';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <TtsProvider>{children}</TtsProvider>
);

/** Resolve the first deferred in fetchQueue, then drain microtasks. */
async function resolveFetch(d: Deferred<FetchResponse>, path: string) {
  await act(async () => {
    d.resolve(makeFetchResponse(path));
    await new Promise<void>(r => setTimeout(r, 0));
  });
}

/** Last URI passed to createAudioPlayer; undefined when never called. */
function lastPlayerUri(): string | undefined {
  const calls = (createAudioPlayer as jest.Mock).mock.calls;
  return calls.at(-1)?.[0]?.uri as string | undefined;
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  fetchQueue.length = 0;  // clear any leftover deferred promises
  jest.clearAllMocks();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('TtsContext — applySettings() staleness guard', () => {

  // ── 1. applySettings() is a no-op when idle ───────────────────────────────

  it('applySettings is a no-op when nothing is playing', () => {
    const { result } = renderHook(() => useTts(), { wrapper });

    expect(result.current.playbackState).toBe('idle');
    act(() => { result.current.applySettings(VOICE_B, SPEED); });
    expect(result.current.playbackState).toBe('idle');
    expect(result.current.session).toBeNull();
    expect(createAudioPlayer as jest.Mock).not.toHaveBeenCalled();
  });

  // ── 2. applySettings() with voice B while A is playing ────────────────────
  //   (no racing change — simple replacement)

  it('applySettings(B) while A is playing creates a player with B voice', async () => {
    // Queue: DA for initial play, DB for applySettings(B)
    const dA = deferred<FetchResponse>();
    const dB = deferred<FetchResponse>();
    fetchQueue.push(dA, dB);

    const { result } = renderHook(() => useTts(), { wrapper });

    // Start playback → DA in-flight
    act(() => { result.current.play(PARTS, DOC_TITLE, DOC_ID, VOICE_A, SPEED); });
    // Resolve DA → player A created
    await resolveFetch(dA, '/voice-A.mp3');

    expect(result.current.playbackState).toBe('playing');
    expect(result.current.session?.voice).toBe(VOICE_A);
    expect(createAudioPlayer as jest.Mock).toHaveBeenCalledTimes(1);
    expect(lastPlayerUri()).toContain('voice-A.mp3');

    // Change to voice B → removes A's player, starts synthesis B
    act(() => { result.current.applySettings(VOICE_B, SPEED); });

    expect(mockRemove).toHaveBeenCalledTimes(1);  // A's player torn down
    // session.voice updated immediately (sync state update in applySettings)
    expect(result.current.session?.voice).toBe(VOICE_B);

    // Resolve DB → player B created
    await resolveFetch(dB, '/voice-B.mp3');

    expect(result.current.playbackState).toBe('playing');
    expect(createAudioPlayer as jest.Mock).toHaveBeenCalledTimes(2);
    expect(lastPlayerUri()).toContain('voice-B.mp3');
  });

  // ── 3. Speed change via applySettings() works correctly ──────────────────

  it('applySettings(same-voice, new-speed) re-synthesises from current part', async () => {
    const dA = deferred<FetchResponse>();
    const dFast = deferred<FetchResponse>();
    fetchQueue.push(dA, dFast);

    const { result } = renderHook(() => useTts(), { wrapper });

    act(() => { result.current.play(PARTS, DOC_TITLE, DOC_ID, VOICE_A, 1.0); });
    await resolveFetch(dA, '/speed-1x.mp3');

    expect(result.current.session?.speed).toBe(1.0);

    act(() => { result.current.applySettings(VOICE_A, 1.5); });
    expect(result.current.session?.speed).toBe(1.5);  // updated immediately

    await resolveFetch(dFast, '/speed-1.5x.mp3');

    expect(result.current.playbackState).toBe('playing');
    expect(lastPlayerUri()).toContain('speed-1.5x.mp3');
  });

  // ── 4. Rapid A → B → C: B resolved late is discarded (mirrors e2e test 4) ─
  //
  //   This is the core staleness scenario the task was created to cover.
  //
  //   Timeline:
  //     play(A)        → DA in-flight
  //     DA resolves    → player A playing
  //     applySettings(B)  → DB in-flight  (counter = N+1)
  //     applySettings(C)  → DC in-flight  (counter = N+2, same sync tick)
  //     DB resolves late  → staleness check fires (N+1 ≠ N+2) → B discarded
  //     DC resolves       → check passes (N+2 = N+2) → player C created

  it('resolves to voice C when B → C change races a late B response (mirror of e2e test 4)', async () => {
    const dA = deferred<FetchResponse>();
    const dB = deferred<FetchResponse>();
    const dC = deferred<FetchResponse>();

    // Fill queue: play(A) pulls dA, applySettings(B) pulls dB, applySettings(C) pulls dC.
    // Both B and C fetches start synchronously within the same act() call (see below).
    fetchQueue.push(dA, dB, dC);

    const { result } = renderHook(() => useTts(), { wrapper });

    // ── Phase 1: start playback with voice A ──────────────────────────────
    act(() => { result.current.play(PARTS, DOC_TITLE, DOC_ID, VOICE_A, SPEED); });
    await resolveFetch(dA, '/voice-A.mp3');

    expect(result.current.playbackState).toBe('playing');
    const createdAfterA = (createAudioPlayer as jest.Mock).mock.calls.length;
    expect(createdAfterA).toBe(1);
    expect(lastPlayerUri()).toContain('voice-A.mp3');

    // ── Phase 2: rapid B then C in the same synchronous tick ─────────────
    //
    // Both applySettings calls run synchronously.  Inside each, playPartAt →
    // synthesizePart calls mobileFetch synchronously (before the await
    // suspends it), so dB and dC are pulled from the queue in the same tick.
    //
    // After this act():
    //   • B's synthesis is awaiting dB.promise (sessionId = N+1)
    //   • C's synthesis is awaiting dC.promise (sessionId = N+2)
    //   • A's player has been removed (by B's applySettings)
    act(() => {
      result.current.applySettings(VOICE_B, SPEED);  // counter → N+1; pulls dB
      result.current.applySettings(VOICE_C, SPEED);  // counter → N+2; pulls dC
    });

    // A's player must have been removed (by the first applySettings call)
    expect(mockRemove).toHaveBeenCalledTimes(1);
    // session.voice updated synchronously to the last applySettings call (C)
    expect(result.current.session?.voice).toBe(VOICE_C);

    // ── Phase 3: resolve B late — must be silently discarded ─────────────
    await resolveFetch(dB, '/voice-B.mp3');

    // B's result should have been discarded: no new player created for B
    expect((createAudioPlayer as jest.Mock).mock.calls.length).toBe(createdAfterA);

    // The last player URI must still be A's (B was thrown away as stale)
    expect(lastPlayerUri()).toContain('voice-A.mp3');

    // playbackState may be 'loading' (C's synthesis still in-flight) or
    // still transitioning — it must NOT be 'playing' with B's audio.
    expect(result.current.session?.voice).toBe(VOICE_C);  // session reflects C

    // ── Phase 4: resolve C — must create a player with C's audio ─────────
    await resolveFetch(dC, '/voice-C.mp3');

    expect(result.current.playbackState).toBe('playing');
    expect(createAudioPlayer as jest.Mock).toHaveBeenCalledTimes(createdAfterA + 1);

    // The player created after C resolved must have C's URI
    expect(lastPlayerUri()).toContain('voice-C.mp3');
    // B's path must never have been used to create a player
    const allUris = (createAudioPlayer as jest.Mock).mock.calls.map((c: any[]) => c[0].uri as string);
    expect(allUris.some(u => u.includes('voice-B.mp3'))).toBe(false);

    expect(result.current.session?.voice).toBe(VOICE_C);
    expect(result.current.session?.docId).toBe(DOC_ID);
    expect(mockPlay).toHaveBeenCalledTimes(createdAfterA + 1);  // A + C (not B)
  });

  // ── 5. Triple rapid change A → B → C → D: only D plays ──────────────────

  it('only the last voice plays when three voice changes race each other', async () => {
    const dA = deferred<FetchResponse>();
    const dB = deferred<FetchResponse>();
    const dC = deferred<FetchResponse>();
    const dD = deferred<FetchResponse>();
    const VOICE_D = 'af_sky';

    fetchQueue.push(dA, dB, dC, dD);

    const { result } = renderHook(() => useTts(), { wrapper });

    act(() => { result.current.play(PARTS, DOC_TITLE, DOC_ID, VOICE_A, SPEED); });
    await resolveFetch(dA, '/voice-A.mp3');
    expect(result.current.playbackState).toBe('playing');

    // Three rapid changes in one tick: B, C, D all in-flight simultaneously
    act(() => {
      result.current.applySettings(VOICE_B, SPEED);  // counter N+1, pulls dB
      result.current.applySettings(VOICE_C, SPEED);  // counter N+2, pulls dC
      result.current.applySettings(VOICE_D, SPEED);  // counter N+3, pulls dD
    });

    expect(result.current.session?.voice).toBe(VOICE_D);

    // Resolve B and C (both stale)
    await resolveFetch(dB, '/voice-B.mp3');
    await resolveFetch(dC, '/voice-C.mp3');

    // No new player created for B or C
    expect((createAudioPlayer as jest.Mock).mock.calls.length).toBe(1); // only A

    // Resolve D — must win
    await resolveFetch(dD, '/voice-D.mp3');

    expect(result.current.playbackState).toBe('playing');
    expect(createAudioPlayer as jest.Mock).toHaveBeenCalledTimes(2); // A + D
    expect(lastPlayerUri()).toContain('voice-D.mp3');

    const allUris = (createAudioPlayer as jest.Mock).mock.calls.map((c: any[]) => c[0].uri as string);
    expect(allUris.some(u => u.includes('voice-B.mp3') || u.includes('voice-C.mp3'))).toBe(false);
  });

  // ── 6. stop() during in-flight synthesis prevents the player from starting ─

  it('stop() called while synthesis is in-flight prevents the player from starting', async () => {
    const dA = deferred<FetchResponse>();
    fetchQueue.push(dA);

    const { result } = renderHook(() => useTts(), { wrapper });

    act(() => { result.current.play(PARTS, DOC_TITLE, DOC_ID, VOICE_A, SPEED); });
    // Don't resolve dA yet — synthesis is in-flight

    expect(result.current.playbackState).toBe('loading');

    // Stop before synthesis resolves
    act(() => { result.current.stop(); });

    expect(result.current.playbackState).toBe('idle');
    expect(result.current.session).toBeNull();

    // Now resolve dA — should be stale and silently discarded
    await resolveFetch(dA, '/voice-A.mp3');

    // Still idle — no player was created
    expect(result.current.playbackState).toBe('idle');
    expect(createAudioPlayer as jest.Mock).not.toHaveBeenCalled();
  });
});
