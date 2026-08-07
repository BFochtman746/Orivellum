/**
 * TtsContext — playback persistence across navigation.
 *
 * These tests verify the core guarantee that audio continues to play when the
 * user navigates away from the document detail screen:
 *
 *   (a) playbackState stays 'playing' after the detail screen unmounts
 *   (b) a newly-mounted consumer sees a live session (not null)
 *   (c) the context exposes the correct part index after navigation back
 *   (d) stop() from the mini-player resets everything to 'idle'
 *
 * TtsProvider lives at the root layout, so its state is independent of any
 * screen component's lifecycle. Navigation = one screen unmounts, another
 * mounts; the provider (and therefore the hook's return value) never changes.
 */

import React from 'react';
import { act, render, renderHook } from '@testing-library/react';
import { TtsProvider, useTts } from '../context/TtsContext';

// ── Mocks ─────────────────────────────────────────────────────────────────────

const mockPlay   = jest.fn();
const mockPause  = jest.fn();
const mockRemove = jest.fn();

/**
 * Captured when the player fires the 'playbackStatusUpdate' event —
 * tests call this to simulate natural end-of-part.
 */
let statusListener: ((status: {
  playing: boolean;
  currentTime: number;
  duration: number;
}) => void) | undefined;

// expo-audio: override the module-level mock with per-test control
jest.mock('expo-audio', () => ({
  createAudioPlayer: jest.fn(() => ({
    play:   mockPlay,
    pause:  mockPause,
    remove: mockRemove,
    addListener: jest.fn(
      (event: string, cb: (s: { playing: boolean; currentTime: number; duration: number }) => void) => {
        if (event === 'playbackStatusUpdate') statusListener = cb;
        return { remove: jest.fn() };
      },
    ),
  })),
  setAudioModeAsync: jest.fn(() => Promise.resolve()),
}));

// mobileFetch: resolves with a TTS serve-path so synthesizePart succeeds
jest.mock('../lib/api', () => ({
  mobileFetch: jest.fn(() => Promise.resolve({
    ok: true,
    json: jest.fn(() => Promise.resolve({ path: '/outputs/tts-part.mp3' })),
  })),
}));

// token helper used inside playPartAt
jest.mock('../lib/token', () => ({
  getApiToken: jest.fn(() => 'test-token'),
}));

// react-native Alert is not available in jsdom; stub it out
jest.mock('react-native', () => ({
  Alert: { alert: jest.fn() },
}));

// ── Fixtures ──────────────────────────────────────────────────────────────────

const PARTS     = ['First part text sentence.', 'Second part text sentence.'];
const DOC_ID    = 'doc-test-001';
const DOC_TITLE = 'Test Document';
const VOICE     = 'en-us-1';
const SPEED     = 1.0;

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <TtsProvider>{children}</TtsProvider>
);

/** Call play() and drain the microtask queue so the full async chain settles. */
async function startPlayback(
  result: { current: ReturnType<typeof useTts> },
  parts = PARTS,
) {
  await act(async () => {
    result.current.play(parts, DOC_TITLE, DOC_ID, VOICE, SPEED);
  });
  await act(async () => {
    await new Promise<void>(r => setTimeout(r, 0));
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('TtsContext — playback and navigation persistence', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    statusListener = undefined;
  });

  // ── 1. Basic play → playing ────────────────────────────────────────────────

  it('reaches "playing" state after synthesis completes', async () => {
    const { result } = renderHook(() => useTts(), { wrapper });

    expect(result.current.playbackState).toBe('idle');
    await startPlayback(result);

    expect(result.current.playbackState).toBe('playing');
    expect(result.current.session?.docId).toBe(DOC_ID);
    expect(result.current.session?.docTitle).toBe(DOC_TITLE);
    expect(result.current.index).toBe(0);
    expect(mockPlay).toHaveBeenCalledTimes(1);
  });

  // ── 2. State survives a consumer unmounting — actual lifecycle boundary ───────
  //
  //  Pattern: mount TtsProvider + consumer, start playback, unmount the consumer
  //  (React element becomes null — simulates navigating away from library/[id]),
  //  then mount a fresh consumer under the same provider (navigating back).
  //  The provider is kept alive across all three rerender() calls because it
  //  occupies the same tree position with the same type and no key change;
  //  React's reconciliation preserves its state.

  it('playbackState stays "playing" when the document consumer unmounts and player is not torn down', async () => {
    // Capture TtsContext value via a side-effectful component that writes to
    // a local variable on every render.
    let captured: ReturnType<typeof useTts> | null = null;
    function Consumer() {
      const tts = useTts();
      captured = tts;
      return null;
    }

    // Mount provider + consumer
    const { rerender } = render(
      <TtsProvider>
        <Consumer />
      </TtsProvider>,
    );

    // Start playback through the consumer
    await act(async () => {
      captured!.play(PARTS, DOC_TITLE, DOC_ID, VOICE, SPEED);
      await new Promise<void>(r => setTimeout(r, 0));
    });

    expect(captured!.playbackState).toBe('playing');

    // ── Navigate away: unmount the screen component ────────────────────────
    // TtsProvider stays mounted at the same tree position.
    rerender(
      <TtsProvider>
        {null}
      </TtsProvider>,
    );

    // Allow multiple ticks — no cleanup effect should have fired
    await act(async () => { await new Promise<void>(r => setTimeout(r, 50)); });

    // Player must NOT have been stopped or removed
    expect(mockRemove).not.toHaveBeenCalled();
    expect(mockPause).not.toHaveBeenCalled();
  });

  // ── 3. Re-mounted consumer reads live session (navigate back) ─────────────

  it('newly-mounted consumer reads the active session and part index after navigating back', async () => {
    let captured: ReturnType<typeof useTts> | null = null;
    function Consumer() {
      const tts = useTts();
      captured = tts;
      return null;
    }

    const { rerender } = render(
      <TtsProvider>
        <Consumer />
      </TtsProvider>,
    );

    await act(async () => {
      captured!.play(PARTS, DOC_TITLE, DOC_ID, VOICE, SPEED);
      await new Promise<void>(r => setTimeout(r, 0));
    });

    expect(captured!.playbackState).toBe('playing');

    // Navigate away — consumer unmounts, provider stays
    rerender(<TtsProvider>{null}</TtsProvider>);
    captured = null;  // clear so we can verify it was re-populated

    // Navigate back — mount a fresh consumer under the same provider
    rerender(
      <TtsProvider>
        <Consumer />
      </TtsProvider>,
    );

    // The new consumer must see the live session immediately on mount —
    // exactly what library/[id].tsx does to drive _isThisDoc and localTtsState
    expect(captured).not.toBeNull();
    expect(captured!.playbackState).toBe('playing');
    expect(captured!.session?.docId).toBe(DOC_ID);
    expect(captured!.index).toBe(0);
    expect(mockRemove).not.toHaveBeenCalled();
  });

  // ── 4. stop() resets everything on both mini-player and detail page ─────────

  it('stop() from the mini-player sets playbackState to "idle" on all consumers', async () => {
    const { result } = renderHook(() => useTts(), { wrapper });
    await startPlayback(result);

    expect(result.current.playbackState).toBe('playing');

    act(() => { result.current.stop(); });

    expect(result.current.playbackState).toBe('idle');
    expect(result.current.session).toBeNull();
    expect(result.current.index).toBe(0);
    // The underlying native player must be torn down exactly once
    expect(mockRemove).toHaveBeenCalledTimes(1);
  });

  // ── 5. Auto-advance to next part ──────────────────────────────────────────

  it('auto-advances to part 2 when the end-of-part event fires', async () => {
    const { result } = renderHook(() => useTts(), { wrapper });
    await startPlayback(result);

    expect(result.current.index).toBe(0);
    expect(statusListener).toBeDefined();

    // Simulate the audio player reaching the end of part 1
    await act(async () => {
      statusListener!({ playing: false, currentTime: 5, duration: 5 });
      await new Promise<void>(r => setTimeout(r, 0));
    });

    expect(result.current.index).toBe(1);
    expect(result.current.playbackState).toBe('playing');
    expect(mockPlay).toHaveBeenCalledTimes(2);  // once per part
  });

  // ── 6. Last part finished → idle ──────────────────────────────────────────

  it('transitions to idle after the final part finishes', async () => {
    const { result } = renderHook(() => useTts(), { wrapper });
    await startPlayback(result, ['Only one part.']);

    expect(result.current.playbackState).toBe('playing');
    expect(statusListener).toBeDefined();

    await act(async () => {
      statusListener!({ playing: false, currentTime: 3, duration: 3 });
      await new Promise<void>(r => setTimeout(r, 0));
    });

    expect(result.current.playbackState).toBe('idle');
    expect(result.current.session).toBeNull();
    expect(result.current.index).toBe(0);
  });

  // ── 7. skipTo jumps to the requested part ─────────────────────────────────

  it('skipTo(1) cancels current part and starts part 2', async () => {
    const { result } = renderHook(() => useTts(), { wrapper });
    await startPlayback(result);

    expect(result.current.index).toBe(0);

    await act(async () => {
      result.current.skipTo(1);
      await new Promise<void>(r => setTimeout(r, 0));
    });

    expect(result.current.index).toBe(1);
    expect(result.current.playbackState).toBe('playing');
    // Old player must have been removed before the new one started
    expect(mockRemove).toHaveBeenCalled();
    expect(mockPlay).toHaveBeenCalledTimes(2);
  });

  // ── 8. skipTo is a no-op when idle ────────────────────────────────────────

  it('skipTo is a no-op when TTS is idle', () => {
    const { result } = renderHook(() => useTts(), { wrapper });

    expect(result.current.playbackState).toBe('idle');
    act(() => { result.current.skipTo(0); });
    expect(result.current.playbackState).toBe('idle');
    expect(mockPlay).not.toHaveBeenCalled();
  });

  // ── 9. pause / resume don't tear down the player ──────────────────────────

  it('pause() and resume() update playbackState without removing the player', async () => {
    const { result } = renderHook(() => useTts(), { wrapper });
    await startPlayback(result);

    act(() => { result.current.pause(); });
    expect(result.current.playbackState).toBe('paused');
    expect(mockPause).toHaveBeenCalledTimes(1);
    expect(mockRemove).not.toHaveBeenCalled();

    act(() => { result.current.resume(); });
    expect(result.current.playbackState).toBe('playing');
    // play() called twice: initial start + resume
    expect(mockPlay).toHaveBeenCalledTimes(2);
    expect(mockRemove).not.toHaveBeenCalled();
  });
});
