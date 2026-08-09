/**
 * Global Read Aloud engine — a React context that owns chunked TTS playback
 * so audio keeps playing (and its docked player stays visible) while the
 * user navigates between pages.
 *
 * Engine rules (see .agents/memory/orivellum-read-aloud.md):
 *  - Every async step is guarded by a monotonic session id captured before
 *    the first await and re-checked after every await; stale results are
 *    discarded before any blob URL is created.
 *  - Concurrent synthesis is deduplicated with a promise map (single-flight).
 *  - The first part is never autoplayed from async code (iOS Safari blocks
 *    it) — the user taps play on the dock.
 *  - The whole text is covered lazily: parts synthesize on demand with a
 *    next-part prefetch, and far-behind blobs are evicted.
 *
 * Two session kinds:
 *  - "tts": text is split into parts and synthesized via POST /studio/tts
 *  - "url": a ready-made audio file (e.g. a Studio output) plays directly
 */
import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { apiFetch } from "@/lib/auth";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Voice + speed options (shared catalog) ────────────────────────────────────

export const TTS_VOICE_OPTIONS = [
  // American Female
  { id: "af_heart",   label: "Heart",   accent: "♀ US" },
  { id: "af_bella",   label: "Bella",   accent: "♀ US" },
  { id: "af_nova",    label: "Nova",    accent: "♀ US" },
  { id: "af_sarah",   label: "Sarah",   accent: "♀ US" },
  { id: "af_jessica", label: "Jessica", accent: "♀ US" },
  { id: "af_nicole",  label: "Nicole",  accent: "♀ US" },
  { id: "af_sky",     label: "Sky",     accent: "♀ US" },
  { id: "af_kore",    label: "Kore",    accent: "♀ US" },
  { id: "af_aoede",   label: "Aoede",   accent: "♀ US" },
  { id: "af_river",   label: "River",   accent: "♀ US" },
  // American Male
  { id: "am_adam",    label: "Adam",    accent: "♂ US" },
  { id: "am_echo",    label: "Echo",    accent: "♂ US" },
  { id: "am_eric",    label: "Eric",    accent: "♂ US" },
  { id: "am_liam",    label: "Liam",    accent: "♂ US" },
  { id: "am_onyx",    label: "Onyx",    accent: "♂ US" },
  { id: "am_fenrir",  label: "Fenrir",  accent: "♂ US" },
  { id: "am_puck",    label: "Puck",    accent: "♂ US" },
  // British Female
  { id: "bf_emma",     label: "Emma",     accent: "♀ UK" },
  { id: "bf_alice",    label: "Alice",    accent: "♀ UK" },
  { id: "bf_isabella", label: "Isabella", accent: "♀ UK" },
  { id: "bf_lily",     label: "Lily",     accent: "♀ UK" },
  // British Male
  { id: "bm_george", label: "George", accent: "♂ UK" },
  { id: "bm_daniel", label: "Daniel", accent: "♂ UK" },
  { id: "bm_fable",  label: "Fable",  accent: "♂ UK" },
  { id: "bm_lewis",  label: "Lewis",  accent: "♂ UK" },
] as const;

export const TTS_SPEED_OPTIONS = [
  { value: 0.75, label: "0.75×" },
  { value: 1.0,  label: "1×" },
  { value: 1.25, label: "1.25×" },
  { value: 1.5,  label: "1.5×" },
] as const;

const TTS_LS_VOICE = "orivellum:tts_voice";
const TTS_LS_SPEED = "orivellum:tts_speed";

// The TTS endpoint caps requests at 10 000 chars; stay well under it and
// split at paragraph/sentence boundaries so parts sound natural.
const TTS_PART_CHARS = 4500;
const TTS_KEEP_BEHIND = 1; // already-played parts kept cached for quick back-seek
const TTS_STALE = "tts-session-stale";

/** Split text into ≤TTS_PART_CHARS parts at paragraph/sentence boundaries. */
export function splitTextForTts(text: string): string[] {
  const paras = text.replace(/\n{3,}/g, "\n\n").split(/\n\n+/);
  const parts: string[] = [];
  let cur = "";
  const flush = () => { if (cur.trim()) parts.push(cur.trim()); cur = ""; };
  for (const p of paras) {
    if (p.length > TTS_PART_CHARS) {
      for (const s of p.split(/(?<=[.!?])\s+/)) {
        if (cur && cur.length + s.length + 1 > TTS_PART_CHARS) flush();
        cur += (cur ? " " : "") + s;
        while (cur.length > TTS_PART_CHARS) { // pathological unbroken text
          parts.push(cur.slice(0, TTS_PART_CHARS));
          cur = cur.slice(TTS_PART_CHARS);
        }
      }
    } else {
      if (cur && cur.length + p.length + 2 > TTS_PART_CHARS) flush();
      cur += (cur ? "\n\n" : "") + p;
    }
  }
  flush();
  return parts;
}

/** Strip markdown decoration so chat replies read naturally aloud. */
export function stripForSpeech(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, " Code block omitted. ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")   // images → alt text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")    // links → label
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/(\*\*|__|\*|_|~~)/g, "")
    .replace(/^\s*\|.*\|\s*$/gm, (row) => row.replace(/\|/g, ", "))
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

// ── Context shape ─────────────────────────────────────────────────────────────

export interface NowPlaying {
  title: string;
  /** In-app route to jump back to the source (document, chat, studio). */
  href?: string;
  kind: "tts" | "url";
}

interface ReadAloudCtx {
  nowPlaying: NowPlaying | null;
  loading: boolean;
  playing: boolean;
  chunkCount: number;
  index: number;
  /** Currently loaded media URL (blob: for TTS parts, server URL for files). */
  mediaUrl: string | null;
  voice: string;
  speed: number;
  audioRef: React.RefObject<HTMLAudioElement | null>;
  /** Start reading text aloud (chunked TTS). Resolves when part 1 is ready. */
  startText: (opts: { title: string; href?: string; text: string }) => Promise<void>;
  /** Play a ready audio file URL in the dock (starts immediately). */
  startUrl: (opts: { title: string; href?: string; url: string }) => void;
  toggle: () => void;
  goToPart: (i: number, autoplay: boolean) => Promise<void>;
  close: () => void;
  applySettings: (voice: string, speed: number) => Promise<void>;
  /** Wire-up handlers for the dock's <audio> element. */
  onEnded: () => void;
  onPlay: () => void;
  onPause: () => void;
  onError: (msg: string) => void;
}

const Ctx = createContext<ReadAloudCtx | null>(null);

export function useReadAloud(): ReadAloudCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useReadAloud must be used within ReadAloudProvider");
  return ctx;
}

// ── Provider ──────────────────────────────────────────────────────────────────

export function ReadAloudProvider({
  children,
  onFail,
}: {
  children: React.ReactNode;
  /** Error reporter (toast); injected so this lib has no UI dependency. */
  onFail?: (message: string) => void;
}) {
  const [nowPlaying, setNowPlaying] = useState<NowPlaying | null>(null);
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [chunks, setChunks] = useState<string[]>([]);
  const [index, setIndex] = useState(0);
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [voice, setVoice] = useState<string>(() => localStorage.getItem(TTS_LS_VOICE) ?? "af_heart");
  const [speed, setSpeed] = useState<number>(() => parseFloat(localStorage.getItem(TTS_LS_SPEED) ?? "1"));

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlCacheRef = useRef<Map<number, string>>(new Map());       // part index → blob URL
  const promisesRef = useRef<Map<number, Promise<string>>>(new Map()); // in-flight single-flight
  const autoPlayRef = useRef(false); // play as soon as the next part's URL lands
  // Monotonic session id — bumped on close, new read, and settings change.
  // Any synthesis result from an older session is discarded (never cached).
  const sessionRef = useRef(0);
  // Refs mirror state so async callbacks always read current values
  const voiceRef = useRef(voice);
  const speedRef = useRef(speed);
  const chunksRef = useRef<string[]>([]);
  const indexRef = useRef(0);
  const lastSrcRef = useRef<string | null>(null);
  // The source we currently WANT loaded. The effect below ignores any state
  // commit that doesn't match this, so a stale `mediaUrl=null` commit from
  // reset() can never pause/clear a source that startUrl() just set
  // synchronously inside the user's tap gesture.
  const desiredSrcRef = useRef<string | null>(null);
  const fail = useCallback((msg: string) => { onFail?.(msg); }, [onFail]);

  // Load the current media URL into the persistent <audio> element.  The
  // element is uncontrolled (no src prop) so startUrl can also set src and
  // call play() synchronously inside the user's tap gesture.
  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    if (mediaUrl !== desiredSrcRef.current) return; // obsolete commit — skip
    if (!mediaUrl) {
      el.pause();
      el.removeAttribute("src");
      lastSrcRef.current = null;
      return;
    }
    if (lastSrcRef.current !== mediaUrl) {
      el.src = mediaUrl;
      lastSrcRef.current = mediaUrl;
    }
    if (autoPlayRef.current) {
      autoPlayRef.current = false;
      el.play().catch(() => setPlaying(false));
    }
  }, [mediaUrl]);

  /** Synthesize one part, cache the blob URL, and return it.
   *  Single-flight per part; results from an older session are discarded
   *  before any blob URL is created. */
  const synthesizePart = useCallback((parts: string[], i: number, v: string, s: number): Promise<string> => {
    const session = sessionRef.current;
    const cached = urlCacheRef.current.get(i);
    if (cached) return Promise.resolve(cached);
    const inflight = promisesRef.current.get(i);
    if (inflight) return inflight;
    const p = (async () => {
      const resp = await apiFetch(`${BASE}/studio/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: parts[i], voice: v, speed: s }),
      });
      if (sessionRef.current !== session) throw new Error(TTS_STALE);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      if (sessionRef.current !== session) throw new Error(TTS_STALE);
      const url = URL.createObjectURL(blob);
      urlCacheRef.current.set(i, url);
      return url;
    })();
    p.finally(() => {
      if (promisesRef.current.get(i) === p) promisesRef.current.delete(i);
    }).catch(() => {}); // avoid unhandled-rejection from the tracking chain
    promisesRef.current.set(i, p);
    return p;
  }, []);

  /** Fire-and-forget prefetch of the next part so playback never stalls. */
  const prefetchPart = useCallback((parts: string[], i: number, v: string, s: number) => {
    if (i >= parts.length) return;
    synthesizePart(parts, i, v, s).catch(() => {});
  }, [synthesizePart]);

  /** Revoke cached blob URLs far behind the current part so memory stays
   *  bounded on arbitrarily long documents. */
  const evictOldParts = useCallback((current: number) => {
    for (const [idx, url] of urlCacheRef.current) {
      if (idx < current - TTS_KEEP_BEHIND) {
        URL.revokeObjectURL(url);
        urlCacheRef.current.delete(idx);
      }
    }
  }, []);

  const reset = useCallback(() => {
    sessionRef.current++;
    autoPlayRef.current = false;
    audioRef.current?.pause();
    setPlaying(false);
    desiredSrcRef.current = null;
    setMediaUrl(null);
    setChunks([]);
    chunksRef.current = [];
    setIndex(0);
    indexRef.current = 0;
    setNowPlaying(null);
    for (const url of urlCacheRef.current.values()) URL.revokeObjectURL(url);
    urlCacheRef.current.clear();
    promisesRef.current.clear();
  }, []);

  const startText = useCallback(async ({ title, href, text }: { title: string; href?: string; text: string }) => {
    reset();
    setLoading(true);
    // Capture the session BEFORE any await — if the player is closed or a
    // new read starts while synthesis is pending, this run must not touch
    // state belonging to a newer session.
    const session = sessionRef.current;
    try {
      const trimmed = text.trim();
      if (!trimmed) { fail("No text available to read aloud."); return; }
      const parts = splitTextForTts(trimmed);
      setChunks(parts);
      chunksRef.current = parts;
      setIndex(0);
      indexRef.current = 0;
      setNowPlaying({ title, href, kind: "tts" });
      const url = await synthesizePart(parts, 0, voiceRef.current, speedRef.current);
      if (sessionRef.current !== session) return; // closed/superseded meanwhile
      desiredSrcRef.current = url;
      setMediaUrl(url);
      prefetchPart(parts, 1, voiceRef.current, speedRef.current);
      // Do NOT autoplay the first part — iOS Safari blocks audio started
      // from async code. The dock appears; the user taps play.
    } catch (e: any) {
      if (e?.message !== TTS_STALE && sessionRef.current === session) {
        fail(`Read aloud failed: ${e?.message ?? "unknown error"}`);
        reset();
      }
    } finally {
      if (sessionRef.current === session) setLoading(false);
    }
  }, [reset, synthesizePart, prefetchPart, fail]);

  const startUrl = useCallback(({ title, href, url }: { title: string; href?: string; url: string }) => {
    reset();
    setNowPlaying({ title, href, kind: "url" });
    desiredSrcRef.current = url;
    setMediaUrl(url);
    // Set src + play synchronously inside the tap gesture (iOS Safari).
    const el = audioRef.current;
    if (el) {
      el.src = url;
      lastSrcRef.current = url;
      el.play().catch(() => fail("Could not play — tap the play button in the player"));
    } else {
      autoPlayRef.current = true;
    }
  }, [reset, fail]);

  /** Advance to part `i` (auto-play unless this is a manual seek while paused). */
  const goToPart = useCallback(async (i: number, autoplay: boolean) => {
    const parts = chunksRef.current;
    if (i < 0 || i >= parts.length) return;
    const session = sessionRef.current;
    setLoading(true);
    try {
      const url = await synthesizePart(parts, i, voiceRef.current, speedRef.current);
      if (sessionRef.current !== session) return; // closed/superseded meanwhile
      autoPlayRef.current = autoplay;
      setIndex(i);
      indexRef.current = i;
      desiredSrcRef.current = url;
      setMediaUrl(url);
      prefetchPart(parts, i + 1, voiceRef.current, speedRef.current);
      evictOldParts(i);
    } catch (e: any) {
      if (e?.message !== TTS_STALE && sessionRef.current === session) {
        setPlaying(false);
        fail(`Could not synthesize part ${i + 1}: ${e?.message ?? "unknown error"}`);
      }
    } finally {
      if (sessionRef.current === session) setLoading(false);
    }
  }, [synthesizePart, prefetchPart, evictOldParts, fail]);

  const onEnded = useCallback(() => {
    setPlaying(false);
    if (indexRef.current + 1 < chunksRef.current.length) {
      void goToPart(indexRef.current + 1, true);
    }
  }, [goToPart]);

  const toggle = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    if (!el.paused) {
      el.pause();
      setPlaying(false);
    } else {
      el.play().catch(() => {});
      setPlaying(true);
    }
  }, []);

  const close = useCallback(() => {
    reset();
    setLoading(false);
  }, [reset]);

  /** Change voice and/or speed; clears the part cache so stale audio never
   *  replays. If a TTS session is open, re-synthesizes the current part. */
  const applySettings = useCallback(async (newVoice: string, newSpeed: number) => {
    // Update refs first so any concurrent synthesizePart calls use new values
    voiceRef.current = newVoice;
    speedRef.current = newSpeed;
    setVoice(newVoice);
    setSpeed(newSpeed);
    localStorage.setItem(TTS_LS_VOICE, newVoice);
    localStorage.setItem(TTS_LS_SPEED, String(newSpeed));

    const parts = chunksRef.current;
    // Any live TTS session — even one whose first part is still synthesizing
    // (mediaUrl not yet set) — must be invalidated, or the pending request
    // would install audio rendered with the OLD voice/speed.
    if (parts.length > 0) {
      // Bump the session counter BEFORE capturing it so in-flight synthesis
      // from the old session (including prefetches) discards its results
      // rather than overwriting the new voice's cache entries.
      sessionRef.current++;
      const session = sessionRef.current;
      audioRef.current?.pause();
      for (const url of urlCacheRef.current.values()) URL.revokeObjectURL(url);
      urlCacheRef.current.clear();
      promisesRef.current.clear();
      setPlaying(false);
      setLoading(true);
      try {
        const url = await synthesizePart(parts, indexRef.current, newVoice, newSpeed);
        if (sessionRef.current !== session) return;
        desiredSrcRef.current = url;
        setMediaUrl(url);
        prefetchPart(parts, indexRef.current + 1, newVoice, newSpeed);
      } catch (e: any) {
        if (e?.message !== TTS_STALE && sessionRef.current === session) {
          fail(`Could not apply new voice: ${e?.message ?? "error"}`);
        }
      } finally {
        if (sessionRef.current === session) setLoading(false);
      }
    }
  }, [synthesizePart, prefetchPart, fail]);

  const value = useMemo<ReadAloudCtx>(() => ({
    nowPlaying, loading, playing, chunkCount: chunks.length, index, mediaUrl,
    voice, speed, audioRef,
    startText, startUrl, toggle, goToPart, close, applySettings,
    onEnded,
    onPlay: () => setPlaying(true),
    onPause: () => setPlaying(false),
    onError: fail,
  }), [nowPlaying, loading, playing, chunks.length, index, mediaUrl, voice, speed,
       startText, startUrl, toggle, goToPart, close, applySettings, onEnded, fail]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
