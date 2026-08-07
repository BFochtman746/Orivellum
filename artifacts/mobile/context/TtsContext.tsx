/**
 * Global TTS context — lets audio persist across navigation.
 *
 * The AudioPlayer lives here (not in library/[id].tsx), so navigating away
 * from a document detail page does not unmount the player or stop playback.
 * A sticky mini-player in the tab layout reads this context to show controls.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Alert } from 'react-native';
import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from 'expo-audio';
import { mobileFetch } from '@/lib/api';
import { getApiToken } from '@/lib/token';

// ── Types ────────────────────────────────────────────────────────────────────

export type TtsPlaybackState = 'idle' | 'loading' | 'playing' | 'paused' | 'error';

export interface TtsSession {
  parts: string[];
  docTitle: string;
  docId: string;
  voice: string;
  speed: number;
}

export interface TtsContextValue {
  playbackState: TtsPlaybackState;
  session: TtsSession | null;
  /** Index of the currently playing part (0-based). */
  index: number;
  play: (parts: string[], docTitle: string, docId: string, voice: string, speed: number) => void;
  /**
   * Change voice and/or speed mid-listen.  Clears the path cache and
   * re-synthesises from the current part so the change takes effect
   * immediately rather than waiting for the next Listen invocation.
   * No-op when playbackState is 'idle'.
   */
  applySettings: (voice: string, speed: number) => void;
  /**
   * Jump to a specific part by index (0-based).  Cancels in-flight synthesis
   * for the current part, keeps already-cached paths for other parts (they
   * remain valid for the same document), and starts playback at part `i`.
   * No-op when idle or when `i` is out of bounds.
   */
  skipTo: (i: number) => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
}

// ── Context ──────────────────────────────────────────────────────────────────

const TtsContext = createContext<TtsContextValue>({
  playbackState: 'idle',
  session: null,
  index: 0,
  play: () => {},
  applySettings: () => {},
  skipTo: () => {},
  pause: () => {},
  resume: () => {},
  stop: () => {},
});

// ── Constants ─────────────────────────────────────────────────────────────────

const _TTS_STALE = 'tts-stale';
const _DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';

// ── Provider ─────────────────────────────────────────────────────────────────

export function TtsProvider({ children }: { children: ReactNode }) {
  const [playbackState, setPlaybackState] = useState<TtsPlaybackState>('idle');
  const [session, setSession] = useState<TtsSession | null>(null);
  const [index, setIndex] = useState(0);

  // Audio player refs (not state — we don't want renders on player changes)
  const playerRef = useRef<AudioPlayer | null>(null);
  const sessionCounterRef = useRef(0);
  const pathCacheRef = useRef<Map<number, string>>(new Map());
  const promisesRef = useRef<Map<number, Promise<string>>>(new Map());
  // Mirror refs so applySettings can read the latest state synchronously
  // without relying on state updater closures.
  const sessionRef = useRef<TtsSession | null>(null);
  const indexRef = useRef(0);

  // Keep mirror refs in sync so applySettings can read the current values
  // without closures over stale state.
  sessionRef.current = session;
  indexRef.current   = index;

  /** Synthesize one part and cache its serve-path. Single-flight per part. */
  const synthesizePart = useCallback(
    (parts: string[], i: number, voice: string, speed: number, sessionId: number): Promise<string> => {
      // Bail early if session is already stale
      if (sessionCounterRef.current !== sessionId) {
        return Promise.reject(new Error(_TTS_STALE));
      }

      const cached = pathCacheRef.current.get(i);
      if (cached) return Promise.resolve(cached);

      const inflight = promisesRef.current.get(i);
      if (inflight) return inflight;

      const p = (async () => {
        const res = await mobileFetch(`https://${_DOMAIN}/api/studio/tts`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: parts[i], voice, speed, return_url: true }),
        });
        if (sessionCounterRef.current !== sessionId) throw new Error(_TTS_STALE);
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error((err as any).detail ?? `HTTP ${res.status}`);
        }
        const json = await res.json();
        if (sessionCounterRef.current !== sessionId) throw new Error(_TTS_STALE);
        const path = json.path as string;
        pathCacheRef.current.set(i, path);
        return path;
      })();

      p.finally(() => {
        if (promisesRef.current.get(i) === p) promisesRef.current.delete(i);
      }).catch(() => {});
      promisesRef.current.set(i, p);
      return p;
    },
    [],
  );

  /**
   * Create a player for part `i`, play it, and wire auto-advance to the next
   * part when playback finishes naturally.
   */
  const playPartAt = useCallback(
    async (
      parts: string[],
      i: number,
      voice: string,
      speed: number,
      sessionId: number,
    ) => {
      setPlaybackState('loading');
      try {
        const servePath = await synthesizePart(parts, i, voice, speed, sessionId);
        if (sessionCounterRef.current !== sessionId) return;

        const token = getApiToken();
        const uri = `https://${_DOMAIN}/api/studio/outputs/serve?path=${encodeURIComponent(servePath)}`;

        // Tear down the previous player before creating a new one
        playerRef.current?.remove();
        playerRef.current = null;

        await setAudioModeAsync({ playsInSilentMode: true });
        // Re-check after the async audio-mode setup: a stop() or a new play()
        // that arrived during that await must not be overwritten.
        if (sessionCounterRef.current !== sessionId) return;

        const player = createAudioPlayer({
          uri,
          headers: token ? { authorization: `Bearer ${token}` } : undefined,
        });
        // Guard again after player creation: if stale, discard the player
        // rather than assigning it so it cannot leak or overwrite the active ref.
        if (sessionCounterRef.current !== sessionId) {
          player.remove();
          return;
        }
        playerRef.current = player;
        player.play();

        setIndex(i);
        setPlaybackState('playing');

        // Prefetch next part in the background
        if (i + 1 < parts.length) {
          synthesizePart(parts, i + 1, voice, speed, sessionId).catch(() => {});
        }

        // Auto-advance on natural end of part
        player.addListener('playbackStatusUpdate', (status) => {
          if (
            !status.playing &&
            status.currentTime > 0 &&
            status.duration > 0 &&
            status.currentTime >= status.duration - 0.5
          ) {
            if (sessionCounterRef.current !== sessionId) return;
            const next = i + 1;
            if (next < parts.length) {
              playPartAt(parts, next, voice, speed, sessionId);
            } else {
              // Finished all parts — return to idle
              setPlaybackState('idle');
              setSession(null);
              setIndex(0);
              playerRef.current?.remove();
              playerRef.current = null;
            }
          }
        });
      } catch (e: any) {
        if (e?.message !== _TTS_STALE && sessionCounterRef.current === sessionId) {
          setPlaybackState('error');
          Alert.alert('Read Aloud failed', e?.message ?? 'Could not synthesize audio');
          setTimeout(() => {
            // Only reset if still in error for this session
            if (sessionCounterRef.current === sessionId) {
              setPlaybackState('idle');
            }
          }, 2500);
        }
      }
    },
    [synthesizePart],
  );

  // ── Public API ───────────────────────────────────────────────────────────────

  const play = useCallback(
    (parts: string[], docTitle: string, docId: string, voice: string, speed: number) => {
      // Atomically bump session counter to invalidate any prior in-flight work
      sessionCounterRef.current += 1;
      const sessionId = sessionCounterRef.current;

      // Tear down existing player and clear caches
      playerRef.current?.remove();
      playerRef.current = null;
      pathCacheRef.current.clear();
      promisesRef.current.clear();

      setSession({ parts, docTitle, docId, voice, speed });
      setIndex(0);
      setPlaybackState('loading');

      playPartAt(parts, 0, voice, speed, sessionId);
    },
    [playPartAt],
  );

  /**
   * Change voice and/or speed mid-listen.
   * Reads the current session and index from mirror refs (always current,
   * no closure issues), bumps the session counter to cancel in-flight
   * synthesis, clears the path/promise caches, then restarts playback from
   * the current part with the new settings.  No-op when idle.
   */
  const applySettings = useCallback(
    (voice: string, speed: number) => {
      const currentSession = sessionRef.current;
      if (!currentSession) return; // idle — nothing to do

      const currentIndex = indexRef.current;

      // Bump session counter — invalidates all outstanding synthesis
      sessionCounterRef.current += 1;
      const sessionId = sessionCounterRef.current;

      // Tear down the active player and clear caches so every remaining
      // part is re-synthesised from scratch with the new voice/speed.
      playerRef.current?.remove();
      playerRef.current = null;
      pathCacheRef.current.clear();
      promisesRef.current.clear();

      // Update session state to reflect the new settings
      setSession({ ...currentSession, voice, speed });

      // Restart from the current part
      playPartAt(currentSession.parts, currentIndex, voice, speed, sessionId);
    },
    [playPartAt],
  );

  const skipTo = useCallback(
    (i: number) => {
      const currentSession = sessionRef.current;
      if (!currentSession) return;                              // idle
      if (i < 0 || i >= currentSession.parts.length) return;   // out of bounds

      // Bump counter to cancel any in-flight synthesis for the current part.
      // We deliberately keep pathCacheRef entries: the synthesized file paths
      // are still valid on the server (same document, same voice/speed), so
      // previously cached parts can be served immediately when revisited.
      sessionCounterRef.current += 1;
      const sessionId = sessionCounterRef.current;

      playerRef.current?.remove();
      playerRef.current = null;
      // Clear only in-flight promise refs — stale promises will reject on the
      // next counter check, but we don't want them occupying the slot while
      // the new playPartAt starts its own synthesis.
      promisesRef.current.clear();

      playPartAt(currentSession.parts, i, currentSession.voice, currentSession.speed, sessionId);
    },
    [playPartAt],
  );

  const pause = useCallback(() => {
    playerRef.current?.pause();
    setPlaybackState('paused');
  }, []);

  const resume = useCallback(() => {
    playerRef.current?.play();
    setPlaybackState('playing');
  }, []);

  const stop = useCallback(() => {
    sessionCounterRef.current += 1;
    playerRef.current?.remove();
    playerRef.current = null;
    pathCacheRef.current.clear();
    promisesRef.current.clear();
    setPlaybackState('idle');
    setSession(null);
    setIndex(0);
  }, []);

  return (
    <TtsContext.Provider value={{ playbackState, session, index, play, applySettings, skipTo, pause, resume, stop }}>
      {children}
    </TtsContext.Provider>
  );
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useTts(): TtsContextValue {
  return useContext(TtsContext);
}
