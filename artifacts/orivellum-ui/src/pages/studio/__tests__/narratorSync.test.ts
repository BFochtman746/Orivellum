/**
 * NarratorSync — the manual-narrator persistence contract:
 * pick a narrator → it saves without any Save button; leave and reopen the
 * Work → the saved narrator is the baseline (restored, not re-saved).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NarratorSync } from "../narratorSync";

function makePut() {
  const calls: { workId: string; voiceId: string }[] = [];
  const put = vi.fn(async (workId: string, voiceId: string) => {
    calls.push({ workId, voiceId });
  });
  return { put, calls };
}

async function drain() {
  // Let the promise chain settle after timers fire.
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("NarratorSync", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("never saves before the Work's casting has loaded", async () => {
    const sync = new NarratorSync();
    const { put } = makePut();
    sync.select("w1", "af_heart", put);
    vi.advanceTimersByTime(5000);
    await drain();
    expect(put).not.toHaveBeenCalled();
  });

  it("does not re-save the restored narrator on reopen", async () => {
    const sync = new NarratorSync();
    const { put } = makePut();
    // Reopen: server says af_heart is saved; component restores the picker
    // to af_heart and reports the load.
    sync.noteLoaded("w1", "af_heart", "af_heart");
    sync.select("w1", "af_heart", put); // effect fires with restored value
    vi.advanceTimersByTime(5000);
    await drain();
    expect(put).not.toHaveBeenCalled();
  });

  it("persists a manual narrator change after the debounce", async () => {
    const sync = new NarratorSync();
    const { put, calls } = makePut();
    sync.noteLoaded("w1", null, "bm_george"); // no saved narrator; picker default
    sync.select("w1", "af_heart", put);
    expect(put).not.toHaveBeenCalled(); // debounced
    vi.advanceTimersByTime(600);
    await drain();
    expect(calls).toEqual([{ workId: "w1", voiceId: "af_heart" }]);
    // Selecting the now-saved value again is a no-op (baseline advanced)
    sync.select("w1", "af_heart", put);
    vi.advanceTimersByTime(5000);
    await drain();
    expect(put).toHaveBeenCalledTimes(1);
  });

  it("rapid changes collapse to a single save of the latest pick", async () => {
    const sync = new NarratorSync();
    const { put, calls } = makePut();
    sync.noteLoaded("w1", "af_heart", "af_heart");
    sync.select("w1", "af_sky", put);
    vi.advanceTimersByTime(200);
    sync.select("w1", "am_adam", put);
    vi.advanceTimersByTime(200);
    sync.select("w1", "bm_george", put);
    vi.advanceTimersByTime(600);
    await drain();
    expect(calls).toEqual([{ workId: "w1", voiceId: "bm_george" }]);
  });

  it("switching Works cancels a pending save for the old Work", async () => {
    const sync = new NarratorSync();
    const { put } = makePut();
    sync.noteLoaded("w1", "af_heart", "af_heart");
    sync.select("w1", "am_adam", put);
    // Work switch before the debounce elapses
    sync.noteLoaded("w2", "bm_george", "bm_george");
    vi.advanceTimersByTime(5000);
    await drain();
    expect(put).not.toHaveBeenCalled();
    // and selections for the old Work are ignored entirely
    sync.select("w1", "af_sky", put);
    vi.advanceTimersByTime(5000);
    await drain();
    expect(put).not.toHaveBeenCalled();
  });

  it("flush() fires a pending save immediately (leaving the page)", async () => {
    const sync = new NarratorSync();
    const { put, calls } = makePut();
    sync.noteLoaded("w1", "af_heart", "af_heart");
    sync.select("w1", "am_adam", put);
    sync.flush(); // unmount before the debounce elapsed
    await drain();
    expect(calls).toEqual([{ workId: "w1", voiceId: "am_adam" }]);
  });

  it("a failed save keeps the baseline so the same pick can retry", async () => {
    const sync = new NarratorSync();
    let fail = true;
    const calls: string[] = [];
    const put = async (_w: string, v: string) => {
      calls.push(v);
      if (fail) throw new Error("offline");
    };
    sync.noteLoaded("w1", "af_heart", "af_heart");
    sync.select("w1", "am_adam", put);
    vi.advanceTimersByTime(600);
    await drain();
    expect(calls).toEqual(["am_adam"]);
    // Baseline unchanged → selecting the same voice again retries the save
    fail = false;
    sync.select("w1", "am_adam", put);
    vi.advanceTimersByTime(600);
    await drain();
    expect(calls).toEqual(["am_adam", "am_adam"]);
  });

  it("noteSaved() (Save voices) advances the baseline and cancels pending work", async () => {
    const sync = new NarratorSync();
    const { put } = makePut();
    sync.noteLoaded("w1", null, "bm_george");
    sync.select("w1", "af_heart", put);
    sync.noteSaved("w1", "af_heart"); // Save voices persisted it first
    vi.advanceTimersByTime(5000);
    await drain();
    expect(put).not.toHaveBeenCalled();
  });
});
