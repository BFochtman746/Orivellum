/**
 * useGlobalAudio — custom-line ("try your own line") preview behavior.
 *
 * The custom-line preview shares one global <audio> element with the sample
 * player. These tests lock the flows most likely to regress silently:
 *  - namespaced `custom:<voiceId>` play state never collides with sample play
 *  - a second click while playing toggles pause (no new fetch)
 *  - the request body caps text at 200 characters
 *  - a 503 detail from the server (voice engine down / clone fails closed)
 *    surfaces verbatim in the error toast
 *  - rapid clicks between voices: a stale slow response can never overwrite
 *    the newer selection
 *
 * @vitest-environment jsdom
 */
import React, { forwardRef, useImperativeHandle } from "react";
import { render, act, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("@/lib/auth", () => ({ apiFetch: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import { apiFetch } from "@/lib/auth";
import { toast } from "sonner";
import { useGlobalAudio } from "../useGlobalAudio";

const apiFetchMock = apiFetch as ReturnType<typeof vi.fn>;
const toastErrorMock = toast.error as ReturnType<typeof vi.fn>;

// ── Global stubs (jsdom has no media pipeline / object URLs) ─────────────────

let playSpy: ReturnType<typeof vi.fn>;
let pauseSpy: ReturnType<typeof vi.fn>;

const ORIG_CREATE_OBJECT_URL = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const ORIG_REVOKE_OBJECT_URL = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
const ORIG_MEDIA_PLAY = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "play");
const ORIG_MEDIA_PAUSE = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "pause");

function restoreDescriptor(target: object, prop: string, orig?: PropertyDescriptor) {
  if (orig) Object.defineProperty(target, prop, orig);
  else delete (target as any)[prop];
}

function okAudioResponse(): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers(),
    blob: async () => new Blob(["fake-audio"]),
    json: async () => ({}),
  } as unknown as Response;
}

beforeEach(() => {
  apiFetchMock.mockReset();
  toastErrorMock.mockReset();
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
  restoreDescriptor(URL, "createObjectURL", ORIG_CREATE_OBJECT_URL);
  restoreDescriptor(URL, "revokeObjectURL", ORIG_REVOKE_OBJECT_URL);
  restoreDescriptor(HTMLMediaElement.prototype, "play", ORIG_MEDIA_PLAY);
  restoreDescriptor(HTMLMediaElement.prototype, "pause", ORIG_MEDIA_PAUSE);
});

// ── Hook harness ──────────────────────────────────────────────────────────────

type Handle = { current: ReturnType<typeof useGlobalAudio> };

const Harness = forwardRef<Handle>((_props, ref) => {
  const audio = useGlobalAudio();
  useImperativeHandle(ref, () => ({ current: audio }), [audio]);
  return null;
});
Harness.displayName = "Harness";

function renderHook_() {
  const ref = React.createRef<Handle>();
  render(<Harness ref={ref} />);
  return () => ref.current!.current;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("custom line preview (playCustomLine)", () => {
  it("plays under the namespaced custom:<voiceId> key, not the sample key", async () => {
    apiFetchMock.mockResolvedValue(okAudioResponse());
    const audio = renderHook_();

    await act(async () => { await audio().playCustomLine("nova", "Hello there."); });

    expect(audio().playingId).toBe("custom:nova");
    expect(audio().playingId).not.toBe("nova"); // sample state untouched
    const [url, init] = apiFetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/studio\/tts$/); // one-off route, never /sample (cached)
    expect(init.method).toBe("POST");
  });

  it("does not collide with sample playback: starting a custom line supersedes a playing sample", async () => {
    apiFetchMock.mockResolvedValue(okAudioResponse());
    const audio = renderHook_();

    await act(async () => { await audio().playVoiceSample("nova"); });
    expect(audio().playingId).toBe("nova");

    await act(async () => { await audio().playCustomLine("nova", "My line."); });
    expect(audio().playingId).toBe("custom:nova");
    // The shared element was paused when the custom line took over.
    expect(pauseSpy).toHaveBeenCalled();
  });

  it("second click while playing toggles pause without a new request", async () => {
    apiFetchMock.mockResolvedValue(okAudioResponse());
    const audio = renderHook_();

    await act(async () => { await audio().playCustomLine("nova", "Toggle me."); });
    expect(audio().playingId).toBe("custom:nova");
    const fetchCount = apiFetchMock.mock.calls.length;

    await act(async () => { await audio().playCustomLine("nova", "Toggle me."); });
    expect(audio().playingId).toBeNull();
    expect(pauseSpy).toHaveBeenCalled();
    expect(apiFetchMock.mock.calls.length).toBe(fetchCount); // no second fetch
  });

  it("caps the request body text at 200 characters", async () => {
    apiFetchMock.mockResolvedValue(okAudioResponse());
    const audio = renderHook_();
    const long = "x".repeat(500);

    await act(async () => { await audio().playCustomLine("nova", long); });

    const [, init] = apiFetchMock.mock.calls[0];
    const body = JSON.parse(init.body);
    expect(body.text).toHaveLength(200);
    expect(body.voice).toBe("nova");
  });

  it("surfaces a 503 detail (voice engine down) verbatim in the error toast", async () => {
    const detail = "Cloned voices need the premium voice engine — start it with scripts\\start-voice-sidecar.ps1";
    apiFetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail }),
    } as unknown as Response);
    const audio = renderHook_();

    await act(async () => { await audio().playCustomLine("clone:brian", "A line."); });

    expect(toastErrorMock).toHaveBeenCalledWith(detail);
    expect(audio().playingId).toBeNull();
    expect(audio().loadingId).toBeNull(); // fail closed: no stuck spinner
  });

  it("falls back to a generic message when the failure has no detail", async () => {
    apiFetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => { throw new Error("not json"); },
    } as unknown as Response);
    const audio = renderHook_();

    await act(async () => { await audio().playCustomLine("nova", "A line."); });

    expect(toastErrorMock).toHaveBeenCalledWith("Could not synthesize your line with this voice");
    expect(audio().loadingId).toBeNull();
  });

  it("rapid clicks: a stale slow response never overwrites the newer selection", async () => {
    let resolveFirst!: (r: Response) => void;
    const first = new Promise<Response>(res => { resolveFirst = res; });
    apiFetchMock
      .mockReturnValueOnce(first)                       // voice A — slow
      .mockResolvedValueOnce(okAudioResponse());        // voice B — fast
    const audio = renderHook_();

    let firstCall!: Promise<void>;
    act(() => { firstCall = audio().playCustomLine("alpha", "Line A"); });
    await act(async () => { await audio().playCustomLine("beta", "Line B"); });
    expect(audio().playingId).toBe("custom:beta");
    const playCalls = playSpy.mock.calls.length;

    // The stale response for voice A arrives late — it must be ignored.
    await act(async () => { resolveFirst(okAudioResponse()); await firstCall; });
    expect(audio().playingId).toBe("custom:beta");
    expect(audio().loadingId).toBeNull();
    expect(playSpy.mock.calls.length).toBe(playCalls); // stale blob never played
    expect(toastErrorMock).not.toHaveBeenCalled();
  });

  it("stopAll during an in-flight custom line: the late response never starts playing", async () => {
    let resolveFetch!: (r: Response) => void;
    apiFetchMock.mockReturnValue(new Promise<Response>(res => { resolveFetch = res; }));
    const audio = renderHook_();

    let call!: Promise<void>;
    act(() => { call = audio().playCustomLine("nova", "Stop me mid-flight"); });
    expect(audio().loadingId).toBe("custom:nova");

    act(() => { audio().stopAll(); });
    expect(audio().loadingId).toBeNull();
    expect(audio().playingId).toBeNull();
    const playCalls = playSpy.mock.calls.length;

    // The response lands after the user stopped — it must be discarded.
    await act(async () => { resolveFetch(okAudioResponse()); await call; });
    expect(playSpy.mock.calls.length).toBe(playCalls); // play() never called
    expect(audio().playingId).toBeNull();
    expect(audio().loadingId).toBeNull();
    expect(toastErrorMock).not.toHaveBeenCalled();
  });

  it("rapid clicks: a stale request's failure is silent (no misleading toast)", async () => {
    let rejectFirst!: (r: Response) => void;
    const first = new Promise<Response>(res => { rejectFirst = res; });
    apiFetchMock
      .mockReturnValueOnce(first)                       // voice A — will 503 late
      .mockResolvedValueOnce(okAudioResponse());        // voice B — fast
    const audio = renderHook_();

    let firstCall!: Promise<void>;
    act(() => { firstCall = audio().playCustomLine("alpha", "Line A"); });
    await act(async () => { await audio().playCustomLine("beta", "Line B"); });

    await act(async () => {
      rejectFirst({
        ok: false, status: 503, json: async () => ({ detail: "engine down" }),
      } as unknown as Response);
      await firstCall;
    });
    expect(toastErrorMock).not.toHaveBeenCalled(); // stale failure is noise
    expect(audio().playingId).toBe("custom:beta");
  });
});
