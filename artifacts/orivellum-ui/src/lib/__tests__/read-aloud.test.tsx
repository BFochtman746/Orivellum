/**
 * Read Aloud engine — resume positions + Media Session lock-screen controls.
 *
 * Covers the two features that interact with the engine's session-id system:
 *  - per-document resume positions (localStorage validation, offer thresholds,
 *    finish-clears-save, and the one-shot pending seek bound to session+part)
 *  - Media Session handlers (registered per session, cleared on close, and
 *    zero effect when navigator.mediaSession is unavailable)
 *
 * @vitest-environment jsdom
 */
import React, { useEffect } from "react";
import { render, act, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("@/lib/auth", () => ({ apiFetch: vi.fn() }));

import { apiFetch } from "@/lib/auth";
import {
  ReadAloudProvider,
  useReadAloud,
  getSavedListeningProgress,
  listSavedListeningProgress,
  fetchServerListeningPositions,
  mergeListeningProgress,
  createServerPositionsFetcher,
  useListeningProgressBadges,
  splitTextForTts,
} from "@/lib/read-aloud";

const apiFetchMock = apiFetch as ReturnType<typeof vi.fn>;

const RA_KEY = (docId: string) => `orivellum:ra_pos:${docId}`;

/** Two paragraphs big enough that splitTextForTts yields exactly 2 parts. */
const TWO_PART_TEXT = "A".repeat(3000) + "\n\n" + "B".repeat(3000);

// ── Global stubs (jsdom has no media pipeline / object URLs) ─────────────────

let playSpy: ReturnType<typeof vi.fn>;
let pauseSpy: ReturnType<typeof vi.fn>;

// Originals of the globals we replace, restored after each test so the stubs
// never leak into other test files running in the same worker.
const ORIG_CREATE_OBJECT_URL = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const ORIG_REVOKE_OBJECT_URL = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
const ORIG_MEDIA_PLAY = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "play");
const ORIG_MEDIA_PAUSE = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "pause");

function restoreDescriptor(target: object, prop: string, orig?: PropertyDescriptor) {
  if (orig) Object.defineProperty(target, prop, orig);
  else delete (target as any)[prop];
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/studio/tts")) {
      return {
        ok: true, status: 200,
        blob: async () => new Blob(["fake-audio"]),
        json: async () => ({}),
      } as unknown as Response;
    }
    if (String(url).includes("/read-position")) {
      if (init?.method === "PUT" || init?.method === "DELETE") {
        return { ok: true, status: 200, json: async () => ({}) } as unknown as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
    }
    return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
  });

  let n = 0;
  (URL as any).createObjectURL = vi.fn(() => `blob:mock-${++n}`);
  (URL as any).revokeObjectURL = vi.fn();
  playSpy = vi.fn(() => Promise.resolve());
  pauseSpy = vi.fn();
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true, writable: true, value: playSpy,
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true, writable: true, value: pauseSpy,
  });
});

afterEach(() => {
  cleanup();
  delete (navigator as any).mediaSession;
  delete (globalThis as any).MediaMetadata;
  restoreDescriptor(URL, "createObjectURL", ORIG_CREATE_OBJECT_URL);
  restoreDescriptor(URL, "revokeObjectURL", ORIG_REVOKE_OBJECT_URL);
  restoreDescriptor(HTMLMediaElement.prototype, "play", ORIG_MEDIA_PLAY);
  restoreDescriptor(HTMLMediaElement.prototype, "pause", ORIG_MEDIA_PAUSE);
});

// ── Harness: provider + a consumer that exposes ctx and hosts the <audio> ────

type Ctx = ReturnType<typeof useReadAloud>;
let ctx: Ctx;

function Harness() {
  ctx = useReadAloud();
  // Mirrors the real dock's wiring so lock-screen actions that touch the
  // <audio> element keep the provider's playback state coherent.
  return (
    <audio
      ref={ctx.audioRef}
      data-testid="ra-audio"
      onEnded={ctx.onEnded}
      onPlay={ctx.onPlay}
      onPause={ctx.onPause}
    />
  );
}

function renderEngine(onFail?: (msg: string) => void) {
  const utils = render(
    <ReadAloudProvider onFail={onFail}>
      <Harness />
    </ReadAloudProvider>,
  );
  const audio = utils.getByTestId("ra-audio") as HTMLAudioElement;
  return { ...utils, audio };
}

function seedSavedPos(docId: string, pos: unknown) {
  localStorage.setItem(RA_KEY(docId), JSON.stringify(pos));
}

// ── Saved-position validation (pure localStorage layer) ──────────────────────

describe("saved-position validation", () => {
  it("returns a meaningful saved position", () => {
    seedSavedPos("doc1", { part: 2, time: 12, partCount: 5, savedAt: 1000 });
    expect(getSavedListeningProgress("doc1")).toEqual({ part: 2, partCount: 5 });
  });

  it("drops and deletes corrupt (unparseable) entries", () => {
    localStorage.setItem(RA_KEY("doc1"), "{not-json");
    expect(getSavedListeningProgress("doc1")).toBeNull();
    expect(localStorage.getItem(RA_KEY("doc1"))).toBeNull(); // pruned
  });

  it("drops and deletes structurally invalid entries", () => {
    seedSavedPos("doc1", { part: "two", time: 5, partCount: 4, savedAt: 1 });
    expect(getSavedListeningProgress("doc1")).toBeNull();
    expect(localStorage.getItem(RA_KEY("doc1"))).toBeNull(); // cleaned up
  });

  it("treats a position at/past the end as finished, not resumable", () => {
    seedSavedPos("doc1", { part: 4, time: 0, partCount: 4, savedAt: 1 });
    expect(getSavedListeningProgress("doc1")).toBeNull();
    expect(localStorage.getItem(RA_KEY("doc1"))).toBeNull();
  });

  it("rejects negative time and non-positive partCount", () => {
    seedSavedPos("a", { part: 0, time: -5, partCount: 3, savedAt: 1 });
    seedSavedPos("b", { part: 0, time: 30, partCount: 0, savedAt: 1 });
    expect(getSavedListeningProgress("a")).toBeNull();
    expect(getSavedListeningProgress("b")).toBeNull();
  });

  it("hides trivial progress (a few seconds into part 1)", () => {
    seedSavedPos("doc1", { part: 0, time: 10, partCount: 3, savedAt: 1 });
    expect(getSavedListeningProgress("doc1")).toBeNull();
  });

  it("lists only meaningful positions and survives corrupt neighbours", () => {
    seedSavedPos("good", { part: 1, time: 0, partCount: 3, savedAt: 1 });
    seedSavedPos("bad", { part: -1, time: 0, partCount: 3, savedAt: 1 });
    seedSavedPos("trivial", { part: 0, time: 3, partCount: 3, savedAt: 1 });
    localStorage.setItem("orivellum:unrelated", "x");
    expect(listSavedListeningProgress()).toEqual({
      good: { part: 1, partCount: 3 },
    });
    expect(localStorage.getItem(RA_KEY("bad"))).toBeNull(); // pruned
    expect(localStorage.getItem("orivellum:unrelated")).toBe("x"); // untouched
  });
});

// ── Resume offer thresholds (provider) ───────────────────────────────────────

describe("resume offer", () => {
  it("splits the fixture into exactly two parts (guards the tests below)", () => {
    expect(splitTextForTts(TWO_PART_TEXT)).toHaveLength(2);
  });

  it("offers resume when the saved split still matches the text", async () => {
    seedSavedPos("doc1", { part: 1, time: 50, partCount: 2, savedAt: Date.now() });
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    expect(ctx.resumeOffer).toEqual({ part: 1, time: 50 });
  });

  it("drops the save and offers nothing when partCount no longer matches", async () => {
    seedSavedPos("doc1", { part: 1, time: 50, partCount: 3, savedAt: Date.now() });
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    expect(ctx.resumeOffer).toBeNull();
    expect(localStorage.getItem(RA_KEY("doc1"))).toBeNull(); // stale — dropped
  });

  it("does not offer resume for trivial progress", async () => {
    seedSavedPos("doc1", { part: 0, time: 5, partCount: 2, savedAt: Date.now() });
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    expect(ctx.resumeOffer).toBeNull();
  });

  it("declining the offer forgets the position locally and on the server", async () => {
    seedSavedPos("doc1", { part: 1, time: 50, partCount: 2, savedAt: Date.now() });
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    await act(async () => { ctx.declineResume(); });
    expect(ctx.resumeOffer).toBeNull();
    expect(localStorage.getItem(RA_KEY("doc1"))).toBeNull();
    const del = apiFetchMock.mock.calls.find(
      ([u, i]) => String(u).includes("/library/doc1/read-position") && i?.method === "DELETE",
    );
    expect(del).toBeTruthy();
  });
});

// ── Finishing the document clears the save ───────────────────────────────────

describe("finishing the last part", () => {
  it("clears the saved position locally and on the server", async () => {
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    // Moving to the last part records the position (flushes to the server).
    await act(async () => { await ctx.goToPart(1, false); });
    expect(localStorage.getItem(RA_KEY("doc1"))).not.toBeNull();

    // The last part ends → the document is done: position forgotten.
    await act(async () => { ctx.onEnded(); });
    expect(localStorage.getItem(RA_KEY("doc1"))).toBeNull();
    const del = apiFetchMock.mock.calls.find(
      ([u, i]) => String(u).includes("/library/doc1/read-position") && i?.method === "DELETE",
    );
    expect(del).toBeTruthy();
  });

  it("does NOT clear the save when a middle part ends", async () => {
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    // Part 0 ends → engine advances to part 1 (saving progress), no clear.
    await act(async () => { ctx.onEnded(); });
    expect(ctx.index).toBe(1);
    expect(localStorage.getItem(RA_KEY("doc1"))).not.toBeNull();
    const del = apiFetchMock.mock.calls.find(
      ([u, i]) => String(u).includes("/read-position") && i?.method === "DELETE",
    );
    expect(del).toBeUndefined();
  });
});

// ── Pending seek: bound to session AND part ──────────────────────────────────

describe("pending resume seek", () => {
  async function startWithOfferAndAccept(audio: HTMLAudioElement) {
    seedSavedPos("doc1", { part: 1, time: 50, partCount: 2, savedAt: Date.now() });
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    expect(ctx.resumeOffer).toEqual({ part: 1, time: 50 });
    Object.defineProperty(audio, "duration", { configurable: true, value: 100 });
    await act(async () => { ctx.acceptResume(); });
    expect(ctx.index).toBe(1);
  }

  it("seeks into the resumed part when its metadata loads", async () => {
    const { audio } = renderEngine();
    await startWithOfferAndAccept(audio);
    audio.currentTime = 0;
    await act(async () => {
      audio.dispatchEvent(new Event("loadedmetadata"));
    });
    expect(audio.currentTime).toBe(50);
  });

  it("never seeks after the session was closed (stale listener)", async () => {
    const { audio } = renderEngine();
    await startWithOfferAndAccept(audio);
    audio.currentTime = 0;
    await act(async () => { ctx.close(); }); // bumps the session id
    await act(async () => {
      audio.dispatchEvent(new Event("loadedmetadata"));
    });
    expect(audio.currentTime).toBe(0); // stale seek refused
  });

  it("never seeks a different part than the one that requested it", async () => {
    const { audio } = renderEngine();
    await startWithOfferAndAccept(audio);
    // User jumps back to part 0 before part 1's metadata ever loaded.
    await act(async () => { await ctx.goToPart(0, false); });
    audio.currentTime = 0;
    await act(async () => {
      audio.dispatchEvent(new Event("loadedmetadata"));
    });
    expect(audio.currentTime).toBe(0); // seek was for part 1, not part 0
  });

  it("a tiny saved time resumes the part without any pending seek", async () => {
    seedSavedPos("doc1", { part: 1, time: 2, partCount: 2, savedAt: Date.now() });
    const { audio } = renderEngine();
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    expect(ctx.resumeOffer).toEqual({ part: 1, time: 2 });
    Object.defineProperty(audio, "duration", { configurable: true, value: 100 });
    await act(async () => { ctx.acceptResume(); });
    audio.currentTime = 0;
    await act(async () => {
      audio.dispatchEvent(new Event("loadedmetadata"));
    });
    expect(audio.currentTime).toBe(0); // ≤3 s — restart the part instead
  });
});

// ── Media Session (lock-screen controls) ─────────────────────────────────────

type Handler = ((details?: any) => void) | null;

function stubMediaSession() {
  const handlers = new Map<string, Handler>();
  const ms = {
    metadata: null as unknown,
    playbackState: "none",
    setActionHandler: vi.fn((action: string, h: Handler) => { handlers.set(action, h); }),
    setPositionState: vi.fn(),
  };
  Object.defineProperty(navigator, "mediaSession", { configurable: true, value: ms });
  (globalThis as any).MediaMetadata = class {
    title: string; artist: string; album: string;
    constructor(init: any) {
      this.title = init?.title; this.artist = init?.artist; this.album = init?.album;
    }
  };
  return { ms, handlers };
}

const ALL_ACTIONS = ["play", "pause", "nexttrack", "previoustrack",
                     "seekforward", "seekbackward", "seekto"];

describe("media session", () => {
  it("registers handlers when a session starts and wires them to the audio element", async () => {
    const { handlers } = stubMediaSession();
    const { audio } = renderEngine();
    await act(async () => {
      await ctx.startText({ title: "My Doc", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    expect(typeof handlers.get("play")).toBe("function");
    expect(typeof handlers.get("pause")).toBe("function");
    expect(typeof handlers.get("seekto")).toBe("function");
    // Multi-part TTS session → track skipping is available.
    expect(typeof handlers.get("nexttrack")).toBe("function");
    expect(typeof handlers.get("previoustrack")).toBe("function");

    // play/pause act on the audio element; its events (dispatched manually —
    // the prototype stubs don't fire them) keep provider state coherent.
    handlers.get("play")!();
    expect(playSpy).toHaveBeenCalled();
    await act(async () => { audio.dispatchEvent(new Event("play")); });
    expect(ctx.playing).toBe(true);
    handlers.get("pause")!();
    expect(pauseSpy).toHaveBeenCalled();
    await act(async () => { audio.dispatchEvent(new Event("pause")); });
    expect(ctx.playing).toBe(false);
    Object.defineProperty(audio, "duration", { configurable: true, value: 100 });
    audio.currentTime = 10;
    handlers.get("seekforward")!();
    expect(audio.currentTime).toBe(20);
    handlers.get("seekbackward")!();
    expect(audio.currentTime).toBe(10);
    handlers.get("seekto")!({ seekTime: 42 });
    expect(audio.currentTime).toBe(42);
    handlers.get("seekto")!({ seekTime: Number.NaN }); // ignored, no crash
    expect(audio.currentTime).toBe(42);

    // nexttrack advances the multi-part session.
    await act(async () => { handlers.get("nexttrack")!(); });
    expect(ctx.index).toBe(1);
  });

  it("disables track skipping for single-source (url) sessions", async () => {
    const { handlers } = stubMediaSession();
    renderEngine();
    await act(async () => {
      ctx.startUrl({ title: "Audiobook", url: "blob:whole-file" });
    });
    expect(typeof handlers.get("play")).toBe("function");
    expect(handlers.get("nexttrack")).toBeNull();
    expect(handlers.get("previoustrack")).toBeNull();
  });

  it("updates metadata per part and clears everything on close", async () => {
    const { ms, handlers } = stubMediaSession();
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "My Doc", text: TWO_PART_TEXT });
    });
    expect((ms.metadata as any)?.title).toBe("My Doc");
    expect((ms.metadata as any)?.album).toBe("Part 1 of 2");
    await act(async () => { await ctx.goToPart(1, false); });
    expect((ms.metadata as any)?.album).toBe("Part 2 of 2");

    await act(async () => { ctx.close(); });
    expect(ms.metadata).toBeNull();
    expect(ms.playbackState).toBe("none");
    for (const a of ALL_ACTIONS) {
      expect(handlers.get(a)).toBeNull(); // no stale lock-screen handlers
    }
  });

  it("re-registers handlers for each new session", async () => {
    const { ms } = stubMediaSession();
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "First", text: TWO_PART_TEXT });
    });
    ms.setActionHandler.mockClear();
    await act(async () => {
      await ctx.startText({ title: "Second", text: TWO_PART_TEXT });
    });
    const registered = ms.setActionHandler.mock.calls
      .filter(([, h]) => typeof h === "function")
      .map(([a]) => a);
    for (const a of ALL_ACTIONS) expect(registered).toContain(a);
    expect((ms.metadata as any)?.title).toBe("Second");
  });

  it("a handler retained from an old session can never drive the new one", async () => {
    const { handlers } = stubMediaSession();
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "First", text: TWO_PART_TEXT });
    });
    const staleNext = handlers.get("nexttrack")!; // session A's callback
    await act(async () => {
      await ctx.startText({ title: "Second", text: TWO_PART_TEXT });
    });
    expect(ctx.index).toBe(0);
    await act(async () => { staleNext(); }); // fired after session B started
    expect(ctx.index).toBe(0); // refused — B's playback untouched
    // B's own handler still works.
    await act(async () => { handlers.get("nexttrack")!(); });
    expect(ctx.index).toBe(1);
  });

  it("voice/speed changes keep the lock-screen handlers working", async () => {
    const { handlers } = stubMediaSession();
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT });
    });
    const next = handlers.get("nexttrack")!;
    // applySettings bumps the internal synthesis session but does NOT replace
    // the playing document — the same handlers must keep responding.
    await act(async () => { await ctx.applySettings("am_adam", 1.25); });
    await act(async () => { next(); });
    expect(ctx.index).toBe(1);
  });

  it("has zero effect and never crashes when mediaSession is unavailable", async () => {
    // jsdom's navigator has no mediaSession by default — the engine must
    // start, play, switch parts, and close without touching it.
    expect("mediaSession" in navigator).toBe(false);
    renderEngine();
    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    await act(async () => { await ctx.goToPart(1, true); });
    await act(async () => { ctx.onEnded(); });
    await act(async () => { ctx.close(); });
    expect(ctx.nowPlaying).toBeNull();
  });
});

// ── Cross-device badge merge (server batch + localStorage) ───────────────────

describe("cross-device listening progress merge", () => {
  const serverResp = (positions: unknown[]) =>
    ({ ok: true, status: 200, json: async () => ({ positions }) }) as unknown as Response;

  it("fetches and validates the server batch", async () => {
    apiFetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/library/read-positions")) {
        return serverResp([
          { doc_id: "a", part: 2, time: 10, part_count: 5, saved_at: 100 },
          { doc_id: "bad", part: -1, time: 10, part_count: 5, saved_at: 100 }, // invalid — dropped
          { doc_id: "done", part: 5, time: 0, part_count: 5, saved_at: 100 }, // finished — dropped
        ]);
      }
      return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
    });
    const server = await fetchServerListeningPositions();
    expect(server).toEqual({ a: { part: 2, time: 10, partCount: 5, savedAt: 100 } });
  });

  it("returns null when the server is unreachable or malformed", async () => {
    apiFetchMock.mockImplementation(async () => {
      throw new Error("offline");
    });
    expect(await fetchServerListeningPositions()).toBeNull();
    apiFetchMock.mockImplementation(
      async () => ({ ok: true, status: 200, json: async () => ({}) }) as unknown as Response,
    );
    expect(await fetchServerListeningPositions()).toBeNull();
  });

  it("shows a badge for a server-only position (started on another device)", () => {
    const merged = mergeListeningProgress({
      phoneDoc: { part: 3, time: 0, partCount: 8, savedAt: 100 },
    });
    expect(merged).toEqual({ phoneDoc: { part: 3, partCount: 8 } });
  });

  it("freshest savedAt wins when both sides have a position", () => {
    seedSavedPos("doc1", { part: 1, time: 0, partCount: 4, savedAt: 200 }); // local fresher
    seedSavedPos("doc2", { part: 1, time: 0, partCount: 4, savedAt: 50 }); // server fresher
    const merged = mergeListeningProgress({
      doc1: { part: 3, time: 0, partCount: 4, savedAt: 100 },
      doc2: { part: 2, time: 0, partCount: 4, savedAt: 150 },
    });
    expect(merged.doc1).toEqual({ part: 1, partCount: 4 });
    expect(merged.doc2).toEqual({ part: 2, partCount: 4 });
  });

  it("clears the badge when the freshest copy says barely-started", () => {
    // Local shows meaningful progress, but a FRESHER server copy was reset to
    // the start (e.g. the listen was restarted on another device).
    seedSavedPos("doc1", { part: 2, time: 0, partCount: 4, savedAt: 100 });
    const merged = mergeListeningProgress({
      doc1: { part: 0, time: 2, partCount: 4, savedAt: 300 },
    });
    expect(merged.doc1).toBeUndefined();
  });

  it("keeps a FRESH local-only badge when the server has no copy (sync in flight)", () => {
    seedSavedPos("doc1", { part: 1, time: 0, partCount: 3, savedAt: Date.now() });
    expect(mergeListeningProgress({})).toEqual({ doc1: { part: 1, partCount: 3 } });
    expect(mergeListeningProgress(null)).toEqual({ doc1: { part: 1, partCount: 3 } });
  });

  it("clears a STALE local badge absent from a successful server batch (deleted remotely)", () => {
    // Old local copy, server batch succeeded and has no row for it — the
    // listen was finished/declined on another device; server is authoritative.
    seedSavedPos("doc1", { part: 1, time: 0, partCount: 3, savedAt: Date.now() - 10 * 60 * 1000 });
    expect(mergeListeningProgress({})).toEqual({});
    expect(localStorage.getItem(RA_KEY("doc1"))).toBeNull(); // local copy dropped too
  });

  it("never deletes local copies when the server fetch failed", () => {
    seedSavedPos("doc1", { part: 1, time: 0, partCount: 3, savedAt: Date.now() - 10 * 60 * 1000 });
    expect(mergeListeningProgress(null)).toEqual({ doc1: { part: 1, partCount: 3 } });
    expect(localStorage.getItem(RA_KEY("doc1"))).not.toBeNull();
  });

  it("rejects server rows with a missing or malformed saved_at", async () => {
    apiFetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/library/read-positions")) {
        return serverResp([
          { doc_id: "a", part: 1, time: 0, part_count: 3 }, // no saved_at
          { doc_id: "b", part: 1, time: 0, part_count: 3, saved_at: "yesterday" },
          { doc_id: "c", part: 1, time: 0, part_count: 3, saved_at: -5 },
        ]);
      }
      return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
    });
    expect(await fetchServerListeningPositions()).toEqual({});
  });
});

// ── Clock-skew + fetch-convergence guards ─────────────────────────────────────

describe("clock-skew and fetch convergence", () => {
  const serverResp = (positions: unknown[]) =>
    ({ ok: true, status: 200, json: async () => ({ positions }) }) as unknown as Response;

  it("drops a far-future local copy absent from a successful batch", () => {
    // A broken fast clock must not let a stale badge dodge absence cleanup.
    seedSavedPos("doc1", { part: 1, time: 0, partCount: 3, savedAt: Date.now() + 10 * 3600_000 });
    expect(mergeListeningProgress({})).toEqual({});
    expect(localStorage.getItem(RA_KEY("doc1"))).toBeNull();
  });

  it("lets a sane server copy beat a far-future local copy", () => {
    seedSavedPos("doc1", { part: 1, time: 0, partCount: 4, savedAt: Date.now() + 10 * 3600_000 });
    const merged = mergeListeningProgress({
      doc1: { part: 3, time: 0, partCount: 4, savedAt: Date.now() },
    });
    expect(merged.doc1).toEqual({ part: 3, partCount: 4 }); // server wins — future local distrusted
  });

  it("re-fetches after an in-flight response when a request arrives mid-flight", async () => {
    // First (stale) batch resolves only after a second request was made —
    // e.g. a DELETE landed while the fetch was in flight. The fetcher must
    // issue a follow-up fetch so the final state reflects the deletion.
    let resolveFirst!: (r: Response) => void;
    const first = new Promise<Response>((res) => { resolveFirst = res; });
    let call = 0;
    apiFetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/library/read-positions")) {
        call += 1;
        if (call === 1) return first;
        return serverResp([]); // post-DELETE truth: empty
      }
      return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
    });
    const updates: object[] = [];
    const fetcher = createServerPositionsFetcher((s) => updates.push(s));
    fetcher.request();
    fetcher.request(); // arrives mid-flight — must queue exactly one follow-up
    resolveFirst(serverResp([
      { doc_id: "gone", part: 2, time: 0, part_count: 5, saved_at: 100 },
    ]));
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    expect(call).toBe(2); // stale response was not trusted as final
    expect(updates[updates.length - 1]).toEqual({}); // converged to post-DELETE state
    fetcher.dispose();
  });
});

// ── Live badge lifecycle (useListeningProgressBadges) ─────────────────────────
//
// The Library's resume badges must react to the player's same-tab
// position-changed event — a listen can finish while the Library is on
// screen (the dock is global), and the badge must vanish WITHOUT a tab
// switch or focus change. Renders the real hook next to the real engine.

function BadgeList() {
  const progress = useListeningProgressBadges();
  return (
    <div>
      {Object.entries(progress).map(([id, p]) => (
        <span key={id} data-testid={`badge-${id}`}>
          Part {p.part + 1} of {p.partCount}
        </span>
      ))}
    </div>
  );
}

describe("live resume badges", () => {
  it("shows a badge when a position is saved and clears it the moment the listen finishes", async () => {
    const utils = render(
      <ReadAloudProvider>
        <Harness />
        <BadgeList />
      </ReadAloudProvider>,
    );
    expect(utils.queryByTestId("badge-doc1")).toBeNull();

    await act(async () => {
      await ctx.startText({ title: "T", text: TWO_PART_TEXT, resumeKey: "doc1" });
    });
    // Reaching the last part saves a position → badge appears immediately.
    await act(async () => { await ctx.goToPart(1, false); });
    expect(utils.getByTestId("badge-doc1").textContent).toBe("Part 2 of 2");

    // The last part ends → the engine clears the save and notifies. The badge
    // must disappear with NO focus/visibility/storage event (no tab switch).
    await act(async () => { ctx.onEnded(); });
    expect(utils.queryByTestId("badge-doc1")).toBeNull();
  });

  it("never renders a badge for a corrupt record with part >= partCount", () => {
    seedSavedPos("corrupt", { part: 3, time: 0, partCount: 3, savedAt: Date.now() });
    seedSavedPos("good", { part: 1, time: 0, partCount: 3, savedAt: Date.now() });
    const utils = render(
      <ReadAloudProvider>
        <BadgeList />
      </ReadAloudProvider>,
    );
    expect(utils.queryByTestId("badge-corrupt")).toBeNull();
    expect(utils.getByTestId("badge-good")).toBeTruthy();
  });
});
