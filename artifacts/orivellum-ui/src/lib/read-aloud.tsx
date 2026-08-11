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

// ── Resume positions (per document) ──────────────────────────────────────────
// Saved under `orivellum:ra_pos:<resumeKey>` so a long document can be picked
// up at the part (and rough time) where the user stopped listening.

const RA_POS_PREFIX = "orivellum:ra_pos:";
const RA_SAVE_EVERY_MS = 10_000;
// Server sync is coarser than the local save so cross-device writes stay
// infrequent; a part change flushes immediately regardless (see saveProgress).
const RA_SERVER_SYNC_MS = 30_000;
// Don't offer resume for trivial progress (a few seconds into part 1).
const RA_MIN_RESUME_SECS = 20;

export interface SavedPos {
  part: number;
  time: number;       // seconds into the part (approximate)
  partCount: number;  // for validation — text may have changed since saving
  savedAt: number;
}

function loadSavedPos(key: string): SavedPos | null {
  let raw: string | null = null;
  try { raw = localStorage.getItem(RA_POS_PREFIX + key); } catch { return null; }
  if (!raw) return null;
  let p: unknown;
  try { p = JSON.parse(raw); } catch { clearSavedPos(key); return null; } // corrupt — drop it
  if (!isValidPos(p)) { clearSavedPos(key); return null; } // invalid shape — drop it
  return p;
}

function isValidPos(p: any): p is SavedPos {
  return !!p &&
    Number.isInteger(p.part) && p.part >= 0 &&
    Number.isFinite(p.time) && p.time >= 0 &&
    Number.isInteger(p.partCount) && p.partCount > 0 &&
    p.part < p.partCount && // a position at/past the end is finished, not resumable
    Number.isFinite(p.savedAt) && p.savedAt >= 0; // required for freshest-wins compares
}

/** Fired on window whenever a saved position is written or cleared, so
 *  same-tab listeners (e.g. the Library's resume badges) can refresh —
 *  the browser's `storage` event only fires in OTHER tabs. */
export const RA_POS_CHANGED_EVENT = "orivellum:ra-pos-changed";

function notifyPosChanged() {
  try { window.dispatchEvent(new Event(RA_POS_CHANGED_EVENT)); } catch { /* SSR/tests */ }
}

function storeSavedPos(key: string, pos: SavedPos) {
  try { localStorage.setItem(RA_POS_PREFIX + key, JSON.stringify(pos)); } catch { /* quota */ }
  notifyPosChanged();
}

function clearSavedPos(key: string) {
  try { localStorage.removeItem(RA_POS_PREFIX + key); } catch { /* ignore */ }
  notifyPosChanged();
}

// ── Library integration ───────────────────────────────────────────────────────
// Lets the Library surface a "resume listening" indicator on documents with a
// saved position. Mirrors the resume-offer gate: trivial progress (a few
// seconds into part 1) is treated as no position at all.

export interface ListeningProgress {
  part: number;       // 0-based index of the part the user stopped in
  partCount: number;  // total parts at the time the position was saved
}

export function getSavedListeningProgress(key: string): ListeningProgress | null {
  const p = loadSavedPos(key);
  if (!p) return null;
  if (p.part === 0 && p.time < RA_MIN_RESUME_SECS) return null;
  return { part: p.part, partCount: p.partCount };
}

/** All documents with a meaningful saved listening position, keyed by resume
 *  key (the document id). Reads localStorage only — cheap enough to call on
 *  every Library mount/focus. */
export function listSavedListeningProgress(): Record<string, ListeningProgress> {
  const out: Record<string, ListeningProgress> = {};
  try {
    // Collect keys first: loadSavedPos() may delete corrupt entries, and
    // mutating localStorage while indexing it would skip entries.
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(RA_POS_PREFIX)) keys.push(k.slice(RA_POS_PREFIX.length));
    }
    for (const docKey of keys) {
      const prog = getSavedListeningProgress(docKey);
      if (prog) out[docKey] = prog;
    }
  } catch { /* localStorage unavailable */ }
  return out;
}

// ── Server-synced positions (cross-device resume) ────────────────────────────
// The document id doubles as the resume key, so positions are keyed per
// document on the server too. All calls are best-effort: a network failure
// never breaks playback — localStorage remains the fast, always-available path.

/** Server-synced listening positions, keyed by document id. */
export type ServerListeningPositions = Record<string, SavedPos>;

/** Fetch ALL server-synced positions in one batch call (no per-doc N+1).
 *  Returns null when the server is unreachable or replies malformed, so
 *  callers can keep their previous copy instead of wrongly clearing badges. */
export async function fetchServerListeningPositions(): Promise<ServerListeningPositions | null> {
  try {
    const resp = await apiFetch(`${BASE}/library/read-positions`);
    if (!resp.ok) return null;
    const data = await resp.json().catch(() => null);
    if (!data || !Array.isArray(data.positions)) return null;
    const out: ServerListeningPositions = {};
    for (const raw of data.positions) {
      const key = raw?.doc_id;
      if (typeof key !== "string" || !key) continue;
      const pos = {
        part: raw.part, time: raw.time,
        partCount: raw.part_count, savedAt: raw.saved_at,
      };
      if (isValidPos(pos)) out[key] = pos;
    }
    return out;
  } catch { return null; }
}

/** How long a local-only position is trusted when a successful server batch
 *  doesn't contain it. Positions are pushed to the server debounced (~30 s)
 *  during playback, so a local copy the server doesn't know about after this
 *  window almost certainly means the position was DELETED on another device
 *  (finished / declined there) — not that the sync is still in flight. */
const RA_SYNC_GRACE_MS = 5 * 60 * 1000;

/** Merge local (localStorage) and server positions into badge shape.
 *  Per key the freshest `savedAt` wins — the same rule the resume offer
 *  uses — and the trivial-progress gate applies to the winner, so a listen
 *  finished/reset on either side clears the badge everywhere.
 *
 *  Deletion semantics: when the batch fetch SUCCEEDED and a local position is
 *  absent from it, the server is authoritative once the local copy is older
 *  than the sync grace window — the stale local copy is dropped (storage
 *  included) instead of showing a badge forever for a listen finished on
 *  another device. Fresh local saves are kept (their PUT may still be in
 *  flight, or the device may be offline). */
export function mergeListeningProgress(
  server: ServerListeningPositions | null,
): Record<string, ListeningProgress> {
  const out = listSavedListeningProgress();
  if (!server) return out; // fetch failed — local-only view, delete nothing
  const now = Date.now();
  // Clock-skew guard: a savedAt more than the grace window in the FUTURE is a
  // broken clock, not a fresh save — treat it as the oldest possible value so
  // it can never win a freshest-wins compare or dodge absence cleanup forever.
  const trustedSavedAt = (p: SavedPos) => (p.savedAt > now + RA_SYNC_GRACE_MS ? 0 : p.savedAt);
  for (const key of Object.keys(out)) {
    if (server[key]) continue;
    const local = loadSavedPos(key);
    if (local && Math.abs(now - local.savedAt) > RA_SYNC_GRACE_MS) {
      clearSavedPos(key); // deleted on another device — server is authoritative
      delete out[key];
    }
  }
  for (const [key, sp] of Object.entries(server)) {
    const local = loadSavedPos(key);
    const pos = local && trustedSavedAt(local) >= trustedSavedAt(sp) ? local : sp;
    if (pos.part === 0 && pos.time < RA_MIN_RESUME_SECS) {
      delete out[key]; // freshest copy says "barely started" — not resumable
      continue;
    }
    out[key] = { part: pos.part, partCount: pos.partCount };
  }
  return out;
}

/** Single-flight batch fetcher that always converges: a request made while a
 *  fetch is in flight queues exactly one follow-up fetch, so state written by
 *  a fire-and-forget PUT/DELETE during the flight is always re-read (an
 *  in-flight response can be stale the moment it arrives). */
export function createServerPositionsFetcher(
  onUpdate: (server: ServerListeningPositions) => void,
): { request: () => void; dispose: () => void } {
  let fetching = false;
  let pending = false;
  let disposed = false;
  const run = () => {
    if (disposed) return;
    if (fetching) { pending = true; return; }
    fetching = true;
    void fetchServerListeningPositions().then((server) => {
      fetching = false;
      if (disposed) return;
      if (server) onUpdate(server); // null = unreachable — keep previous copy
      if (pending) { pending = false; run(); }
    });
  };
  return { request: run, dispose: () => { disposed = true; } };
}

async function fetchServerPos(key: string): Promise<SavedPos | null> {
  try {
    const resp = await apiFetch(`${BASE}/library/${encodeURIComponent(key)}/read-position`);
    if (!resp.ok) return null;
    const data = await resp.json().catch(() => null);
    const raw = data?.position;
    if (!raw) return null;
    const pos = {
      part: raw.part, time: raw.time,
      partCount: raw.part_count, savedAt: raw.saved_at,
    };
    return isValidPos(pos) ? pos : null;
  } catch { return null; }
}

function pushServerPos(key: string, pos: SavedPos): void {
  // Fire-and-forget; keepalive lets the save survive a page unload/tab switch.
  try {
    void apiFetch(`${BASE}/library/${encodeURIComponent(key)}/read-position`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        part: pos.part, time: pos.time,
        part_count: pos.partCount, saved_at: pos.savedAt,
      }),
      keepalive: true,
    }).catch(() => {});
  } catch { /* ignore */ }
}

function clearServerPos(key: string): void {
  try {
    void apiFetch(`${BASE}/library/${encodeURIComponent(key)}/read-position`, {
      method: "DELETE",
      keepalive: true,
    }).catch(() => {});
  } catch { /* ignore */ }
}

// The TTS endpoint caps requests at 10 000 chars; stay well under it and
// split at paragraph/sentence boundaries so parts sound natural.
const TTS_PART_CHARS = 4500;
const TTS_KEEP_BEHIND = 1; // already-played parts kept cached for quick back-seek
const TTS_STALE = "tts-session-stale";

// ~100 ms of 8-bit mono silence. Played synchronously inside the user's tap
// gesture by startLive() to "unlock" the shared <audio> element, so that the
// live session's asynchronously-synthesized parts are allowed to auto-play
// under browser autoplay policies.
const SILENT_WAV =
  "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBAAAAABAAEAESsAABErAAABAAgAZGF0YQQAAACAgICA";

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
  /** Start reading text aloud (chunked TTS). Resolves when part 1 is ready.
   *  Pass `resumeKey` (e.g. the document id) to remember the listening
   *  position and offer to resume next time. */
  startText: (opts: { title: string; href?: string; text: string; resumeKey?: string }) => Promise<void>;
  /** A saved position exists for this session — the dock offers to resume. */
  resumeOffer: { part: number; time: number } | null;
  acceptResume: () => void;
  declineResume: () => void;
  /** Play a ready audio file URL in the dock (starts immediately). */
  startUrl: (opts: { title: string; href?: string; url: string }) => void;
  /** Begin a LIVE spoken-reply session (chat voice mode). MUST be called
   *  synchronously inside a user gesture — it primes the shared audio element
   *  so later, asynchronously-enqueued parts are allowed to auto-play.
   *  `onDone` fires once when every enqueued part has finished playing after
   *  endLive() was called. */
  startLive: (opts: { title: string; href?: string; onDone?: () => void }) => void;
  /** Append a sentence/fragment to the live session's speech queue. */
  enqueueLive: (text: string) => void;
  /** Signal that no more text will be enqueued; onDone fires when playback
   *  drains (immediately if it already has). Safe to call more than once. */
  endLive: () => void;
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
  const [resumeOffer, setResumeOffer] = useState<{ part: number; time: number } | null>(null);

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
  // Resume support: key of the current TTS session (null = don't remember),
  // and a one-shot seek applied when the target part's audio metadata loads.
  // The seek is bound to the session AND part that requested it, so a stale
  // loadedmetadata listener can never seek a newer, unrelated source.
  const resumeKeyRef = useRef<string | null>(null);
  // Mirrors nowPlaying so Media Session handlers can detect they belong to a
  // replaced session (see the action-handler effect below).
  const nowPlayingRef = useRef<NowPlaying | null>(null);
  useEffect(() => { nowPlayingRef.current = nowPlaying; }, [nowPlaying]);
  const pendingSeekRef = useRef<{ session: number; part: number; time: number } | null>(null);
  // Wall-clock (ms) of the last server position push, to throttle sync writes.
  const lastServerSyncRef = useRef(0);
  // Bumped whenever the resume offer is resolved (accepted/declined) so a
  // still-pending server-position fetch can't re-offer or seek after the fact.
  const resumeResolveRef = useRef(0);
  // Live (voice-mode) session state. liveRef marks the session as live;
  // liveOpenRef is true while more text may still be enqueued; liveIdleRef is
  // true when the playback pipeline has drained (nothing playing or pending),
  // so the next enqueueLive() must kick off playback itself.
  const liveRef = useRef(false);
  const liveOpenRef = useRef(false);
  const liveIdleRef = useRef(false);
  const onLiveDoneRef = useRef<(() => void) | null>(null);

  /** Fire the live-done callback exactly once. */
  const fireLiveDone = () => {
    const cb = onLiveDoneRef.current;
    onLiveDoneRef.current = null;
    cb?.();
  };
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
      const seek = pendingSeekRef.current;
      if (seek && seek.session === sessionRef.current && seek.part === indexRef.current) {
        pendingSeekRef.current = null;
        el.addEventListener("loadedmetadata", () => {
          // Re-verify identity at fire time: the listener may outlive the
          // session (reset/new source) — never seek an unrelated source.
          if (sessionRef.current !== seek.session) return;
          if (lastSrcRef.current !== mediaUrl || indexRef.current !== seek.part) return;
          if (isFinite(el.duration) && el.duration > 0) {
            el.currentTime = Math.min(seek.time, Math.max(0, el.duration - 1));
          }
        }, { once: true });
      } else if (seek && seek.session !== sessionRef.current) {
        pendingSeekRef.current = null; // stale request from a dead session
      }
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
      // 503 = "no neural voice engine ready yet" (the server never falls back
      // to a robotic voice). Pause and retry a few times so playback waits
      // for the engine to come up instead of failing instantly.
      const MAX_ATTEMPTS = 4;
      const RETRY_DELAY_MS = 3500;
      let resp: Response;
      for (let attempt = 1; ; attempt++) {
        resp = await apiFetch(`${BASE}/studio/tts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          // quality:"draft" → server skips the premium sidecar so parts start
          // instantly from Kokoro; studio-grade renders are for audiobook builds.
          body: JSON.stringify({ text: parts[i], voice: v, speed: s, quality: "draft" }),
        });
        if (sessionRef.current !== session) throw new Error(TTS_STALE);
        if (resp.status !== 503 || attempt >= MAX_ATTEMPTS) break;
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
        if (sessionRef.current !== session) throw new Error(TTS_STALE);
      }
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        const d = (err as any).detail;
        if (resp.status === 503) {
          throw new Error(
            typeof d === "object" && d?.reason
              ? String(d.reason)
              : "The voice engine isn't ready yet — try again in a moment.",
          );
        }
        throw new Error(
          typeof d === "string" ? d : d ? JSON.stringify(d) : `HTTP ${resp.status}`,
        );
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

  /** Persist the current listening position for the active TTS session.
   *  `partOverride`/`timeOverride` let part changes record the NEW part at
   *  t=0 before the audio element has actually switched. */
  const saveProgress = useCallback((partOverride?: number, timeOverride?: number, flushServer = false) => {
    const key = resumeKeyRef.current;
    const partCount = chunksRef.current.length;
    if (!key || partCount === 0) return;
    const part = partOverride ?? indexRef.current;
    const time = timeOverride ?? (audioRef.current?.currentTime ?? 0);
    // Skip trivial progress so a brief tap of part 1 doesn't nag to resume.
    if (part === 0 && time < RA_MIN_RESUME_SECS) return;
    const pos: SavedPos = { part, time, partCount, savedAt: Date.now() };
    storeSavedPos(key, pos);
    // Server sync is coarser than the local save: push on an explicit flush
    // (part change / close / session swap) or once every RA_SERVER_SYNC_MS,
    // so cross-device writes stay infrequent.
    if (flushServer || pos.savedAt - lastServerSyncRef.current >= RA_SERVER_SYNC_MS) {
      lastServerSyncRef.current = pos.savedAt;
      pushServerPos(key, pos);
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
    resumeKeyRef.current = null;
    pendingSeekRef.current = null;
    setResumeOffer(null);
    liveRef.current = false;
    liveOpenRef.current = false;
    liveIdleRef.current = false;
    onLiveDoneRef.current = null;
  }, []);

  const startText = useCallback(async ({ title, href, text, resumeKey }: { title: string; href?: string; text: string; resumeKey?: string }) => {
    saveProgress(undefined, undefined, true); // remember the outgoing session's place before replacing it
    reset();
    lastServerSyncRef.current = 0; // new session may sync immediately
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
      resumeKeyRef.current = resumeKey ?? null;
      if (resumeKey) {
        // A saved position is offerable only when the split still matches
        // (text unchanged) and the spot is meaningfully past the start.
        const offerFrom = (saved: SavedPos | null): boolean => {
          if (saved && saved.partCount === parts.length
              && saved.part >= 0 && saved.part < parts.length
              && (saved.part > 0 || saved.time >= RA_MIN_RESUME_SECS)) {
            setResumeOffer({ part: saved.part, time: Math.max(0, saved.time) });
            return true;
          }
          return false;
        };
        const local = loadSavedPos(resumeKey);
        // Offer the local position immediately (fast path), then reconcile with
        // the server copy — the freshest of the two wins, so a spot saved on
        // another device (phone <-> desktop) is picked up here.
        const localOffered = offerFrom(local);
        if (!localOffered && local) clearSavedPos(resumeKey); // stale locally
        const resolveGen = resumeResolveRef.current;
        void (async () => {
          const server = await fetchServerPos(resumeKey);
          // Abort if the session was replaced, or the user already accepted /
          // declined the offer while this fetch was in flight (either would
          // make a late re-offer or seek surprising).
          if (sessionRef.current !== session || resumeKeyRef.current !== resumeKey) return;
          if (resumeResolveRef.current !== resolveGen) return;
          if (!server) return;
          // Keep whichever was saved most recently.
          if (local && local.savedAt >= server.savedAt) return;
          if (offerFrom(server)) {
            storeSavedPos(resumeKey, server); // mirror the winner locally
          } else if (!local) {
            clearServerPos(resumeKey); // server copy stale for the current text
          }
        })();
      }
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
    saveProgress(undefined, undefined, true); // remember the outgoing TTS session's place, if any
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
      // Record the new part immediately (t≈0, or the pending resume seek) so
      // a reload right after a part change still resumes at the right part.
      const seek = pendingSeekRef.current;
      saveProgress(i, seek && seek.session === session && seek.part === i ? seek.time : 0, true);
    } catch (e: any) {
      if (e?.message !== TTS_STALE && sessionRef.current === session) {
        setPlaying(false);
        fail(`Could not synthesize part ${i + 1}: ${e?.message ?? "unknown error"}`);
        // Live sessions: a failed part stalls the pipeline (nothing will fire
        // onEnded). Mark the pipeline idle so the next enqueue restarts it,
        // and fire onDone if the text producer already finished.
        if (liveRef.current) {
          liveIdleRef.current = true;
          if (!liveOpenRef.current) fireLiveDone();
        }
      }
    } finally {
      if (sessionRef.current === session) setLoading(false);
    }
  }, [synthesizePart, prefetchPart, evictOldParts, fail]);

  const startLive = useCallback(({ title, href, onDone }: { title: string; href?: string; onDone?: () => void }) => {
    saveProgress(undefined, undefined, true); // remember the outgoing session's place, if any
    reset();
    liveRef.current = true;
    liveOpenRef.current = true;
    liveIdleRef.current = true; // nothing queued yet — first enqueue starts playback
    onLiveDoneRef.current = onDone ?? null;
    setNowPlaying({ title, href, kind: "tts" });
    // Prime the shared <audio> element synchronously inside the caller's tap
    // gesture: playing a moment of silence "unlocks" the element so that the
    // asynchronously-synthesized parts below are allowed to auto-play.
    // desiredSrc guards against reset()'s pending `mediaUrl=null` state commit
    // pausing/clearing the source we just set (same pattern as startUrl).
    desiredSrcRef.current = SILENT_WAV;
    const el = audioRef.current;
    if (el) {
      el.src = SILENT_WAV;
      lastSrcRef.current = SILENT_WAV;
      el.play().catch(() => { /* blocked — dock still appears; user taps play */ });
    }
  }, [saveProgress, reset]);

  const enqueueLive = useCallback((text: string) => {
    if (!liveRef.current || !liveOpenRef.current) return;
    const t = text.trim();
    if (!t) return;
    const parts = [...chunksRef.current, t];
    chunksRef.current = parts;
    setChunks(parts);
    const i = parts.length - 1;
    if (liveIdleRef.current) {
      // Pipeline is drained — this part must kick playback off itself.
      liveIdleRef.current = false;
      void goToPart(i, true);
    } else {
      // Something is already playing/pending; onEnded will advance here.
      // Pre-synthesize so the hand-off is gapless.
      prefetchPart(parts, i, voiceRef.current, speedRef.current);
    }
  }, [goToPart, prefetchPart]);

  const endLive = useCallback(() => {
    if (!liveRef.current) return;
    liveOpenRef.current = false;
    if (liveIdleRef.current) fireLiveDone(); // already drained (or nothing was enqueued)
  }, []);

  const onEnded = useCallback(() => {
    setPlaying(false);
    // Live sessions: the priming silent source ending is NOT a queue event —
    // the first real part may still be synthesizing (goToPart in flight).
    // Advancing or marking the pipeline idle here would double-start playback
    // and skip/reorder the opening sentences. lastSrcRef only moves off the
    // silent WAV once a real part's audio has been installed.
    if (liveRef.current && lastSrcRef.current === SILENT_WAV) return;
    if (indexRef.current + 1 < chunksRef.current.length) {
      void goToPart(indexRef.current + 1, true);
    } else if (liveRef.current) {
      // Live session drained. If more text may still arrive, go idle and wait
      // for the next enqueueLive() to restart playback; otherwise we're done.
      liveIdleRef.current = true;
      if (!liveOpenRef.current) fireLiveDone();
    } else if (resumeKeyRef.current) {
      // Finished the last part — the document is done; forget the position
      // everywhere (local + server) so it doesn't offer to resume the end.
      clearSavedPos(resumeKeyRef.current);
      clearServerPos(resumeKeyRef.current);
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
    saveProgress(undefined, undefined, true); // keep the place across devices — closing isn't finishing
    reset();
    setLoading(false);
  }, [saveProgress, reset]);

  const acceptResume = useCallback(() => {
    if (!resumeOffer) return;
    resumeResolveRef.current++; // a pending server-pos fetch must not re-offer/seek now
    setResumeOffer(null);
    pendingSeekRef.current = resumeOffer.time > 3
      ? { session: sessionRef.current, part: resumeOffer.part, time: resumeOffer.time }
      : null;
    // No autoplay: synthesis is async and the engine's rule is that a session's
    // first audible part is never play()ed from async code (iOS Safari blocks
    // it). Resume positions the player; the user taps play.
    void goToPart(resumeOffer.part, false);
  }, [resumeOffer, goToPart]);

  const declineResume = useCallback(() => {
    resumeResolveRef.current++; // a pending server-pos fetch must not re-offer now
    setResumeOffer(null);
    if (resumeKeyRef.current) {
      clearSavedPos(resumeKeyRef.current);
      clearServerPos(resumeKeyRef.current);
    }
  }, []);

  // Periodic position save while listening (every ~10 s).
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => saveProgress(), RA_SAVE_EVERY_MS);
    return () => clearInterval(id);
  }, [playing, saveProgress]);

  // ── Media Session — lock-screen / control-center controls ──────────────────
  // navigator.mediaSession is often UNDEFINED here (the app regularly runs
  // over plain HTTP via Tailscale, where isSecureContext is false), so every
  // touch is feature-guarded and wrapped: unavailable API = zero effect.

  /** navigator.mediaSession, or null when unavailable (HTTP / old browser). */
  const getMediaSession = (): MediaSession | null => {
    try {
      return typeof navigator !== "undefined" && "mediaSession" in navigator
        ? navigator.mediaSession
        : null;
    } catch { return null; }
  };

  // Metadata: title + part progress, refreshed on part change.
  useEffect(() => {
    const ms = getMediaSession();
    if (!ms) return;
    try {
      if (!nowPlaying || typeof MediaMetadata === "undefined") {
        ms.metadata = null;
        return;
      }
      ms.metadata = new MediaMetadata({
        title: nowPlaying.title,
        artist: "Orivellum",
        album: chunks.length > 1 ? `Part ${index + 1} of ${chunks.length}` : "",
      });
    } catch { /* unsupported — ignore */ }
  }, [nowPlaying, index, chunks.length]);

  // Playback state so the lock screen shows the correct play/pause glyph.
  useEffect(() => {
    const ms = getMediaSession();
    if (!ms) return;
    try {
      ms.playbackState = !nowPlaying ? "none" : playing ? "playing" : "paused";
    } catch { /* ignore */ }
  }, [nowPlaying, playing]);

  // Position state: gives the lock screen an accurate, scrubbable timeline.
  // Without this, iOS infers duration from the audio element, which lags or
  // reports a wrong length for blob-based TTS parts. Guarded twice —
  // mediaSession may be missing (plain HTTP) and setPositionState is newer
  // than the rest of the API — so unavailable = zero effect.
  const updatePositionState = useCallback(() => {
    const ms = getMediaSession();
    if (!ms || typeof ms.setPositionState !== "function") return;
    try {
      const el = audioRef.current;
      if (!nowPlaying || !el || !isFinite(el.duration) || el.duration <= 0) {
        ms.setPositionState(); // clear — no meaningful timeline right now
        return;
      }
      ms.setPositionState({
        duration: el.duration,
        position: Math.max(0, Math.min(el.currentTime, el.duration)),
        playbackRate: el.playbackRate || 1,
      });
    } catch { /* invalid state mid-transition / unsupported — ignore */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nowPlaying]);

  // Refresh on metadata load (covers part changes — each part swaps src),
  // seeks, rate changes, and play/pause transitions.
  useEffect(() => {
    if (!nowPlaying) { updatePositionState(); return; } // clears stale state
    const el = audioRef.current;
    if (!el) return;
    const events = ["loadedmetadata", "durationchange", "seeked", "ratechange", "play", "pause"] as const;
    for (const ev of events) el.addEventListener(ev, updatePositionState);
    updatePositionState();
    return () => { for (const ev of events) el.removeEventListener(ev, updatePositionState); };
  }, [nowPlaying, mediaUrl, updatePositionState]);

  // Periodic refresh while playing guards against drift (the OS extrapolates
  // from the last reported position + rate; blob playback can hiccup).
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(updatePositionState, 5000);
    return () => window.clearInterval(id);
  }, [playing, updatePositionState]);

  // Action handlers. play/pause act directly on the shared <audio> element
  // (its onPlay/onPause events keep React state in sync); next/previous route
  // through goToPart for multi-part TTS sessions. OS-initiated actions count
  // as user gestures, so play() from these handlers is allowed on iOS.
  useEffect(() => {
    const ms = getMediaSession();
    if (!ms?.setActionHandler) return;
    // Bind every handler to the session (nowPlaying identity) that registered
    // it: browsers replace handlers on re-registration, but a callback
    // retained elsewhere (or fired mid-transition) must never act on a NEWER
    // session's playback. applySettings keeps nowPlaying, so voice/speed
    // changes don't invalidate the lock-screen controls.
    const boundTo = nowPlaying;
    const guarded = (h: MediaSessionActionHandler): MediaSessionActionHandler =>
      (details) => {
        if (nowPlayingRef.current !== boundTo) return; // stale — ignore
        h(details);
      };
    const set = (action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
      try { ms.setActionHandler(action, handler ? guarded(handler) : null); } catch { /* unsupported action */ }
    };
    if (!nowPlaying) {
      // No session — leave no stale handlers behind.
      for (const a of ["play", "pause", "nexttrack", "previoustrack",
                       "seekforward", "seekbackward", "seekto"] as MediaSessionAction[]) {
        set(a, null);
      }
      return;
    }
    set("play", () => {
      const el = audioRef.current;
      if (el?.paused) el.play().catch(() => {});
    });
    set("pause", () => { audioRef.current?.pause(); });
    set("seekforward", () => {
      const el = audioRef.current;
      if (el && isFinite(el.duration)) el.currentTime = Math.min(el.duration, el.currentTime + 10);
    });
    set("seekbackward", () => {
      const el = audioRef.current;
      if (el) el.currentTime = Math.max(0, el.currentTime - 10);
    });
    // Lock-screen scrubber → seek within the current part. The "seeked" event
    // listener then re-reports position state, keeping the scrubber honest.
    set("seekto", (details) => {
      const el = audioRef.current;
      if (!el || !isFinite(el.duration)) return;
      const t = details?.seekTime;
      if (typeof t !== "number" || !isFinite(t)) return;
      const clamped = Math.max(0, Math.min(t, el.duration));
      try {
        if (details.fastSeek && typeof el.fastSeek === "function") el.fastSeek(clamped);
        else el.currentTime = clamped;
      } catch { el.currentTime = clamped; }
    });
    const multiPart = nowPlaying.kind === "tts" && chunks.length > 1;
    set("nexttrack", multiPart ? () => {
      if (indexRef.current + 1 < chunksRef.current.length) {
        void goToPart(indexRef.current + 1, true);
      }
    } : null);
    set("previoustrack", multiPart ? () => {
      if (indexRef.current > 0) void goToPart(indexRef.current - 1, true);
    } : null);
    return () => {
      for (const a of ["play", "pause", "nexttrack", "previoustrack",
                       "seekforward", "seekbackward", "seekto"] as MediaSessionAction[]) {
        set(a, null);
      }
    };
  }, [nowPlaying, chunks.length, goToPart]);

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
    startText, startUrl, startLive, enqueueLive, endLive,
    toggle, goToPart, close, applySettings,
    resumeOffer, acceptResume, declineResume,
    onEnded,
    onPlay: () => setPlaying(true),
    onPause: () => setPlaying(false),
    onError: fail,
  }), [nowPlaying, loading, playing, chunks.length, index, mediaUrl, voice, speed,
       startText, startUrl, startLive, enqueueLive, endLive,
       toggle, goToPart, close, applySettings,
       resumeOffer, acceptResume, declineResume, onEnded, fail]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
