/**
 * CustomLinePreview — component-level behavior of the "try your own line" UI,
 * rendered with a REAL useGlobalAudio instance (only transport/media stubbed):
 *  - Enter in the input triggers playback with the typed text
 *  - empty or whitespace-only input never fires a request (Enter or click)
 *  - the play button is disabled while empty and while loading, but stays
 *    enabled while playing so it can pause
 *  - clicking while playing pauses without a new request
 *
 * @vitest-environment jsdom
 */
import React from "react";
import { render, act, cleanup, fireEvent, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("@/lib/auth", () => ({ apiFetch: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import { apiFetch } from "@/lib/auth";
import { useGlobalAudio } from "../useGlobalAudio";
import { CustomLinePreview, CUSTOM_LINE_MAX } from "../CustomLinePreview";

const apiFetchMock = apiFetch as ReturnType<typeof vi.fn>;

// ── Global stubs (same contract as useGlobalAudio.test.tsx) ──────────────────

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

// ── Harness: real hook + component under test ────────────────────────────────

let currentAudio: ReturnType<typeof useGlobalAudio>;

function Wrapper({ voiceId }: { voiceId: string }) {
  const audio = useGlobalAudio();
  currentAudio = audio;
  return <CustomLinePreview voiceId={voiceId} globalAudio={audio} />;
}

function renderPreview(voiceId = "nova") {
  render(<Wrapper voiceId={voiceId} />);
  const input = screen.getByPlaceholderText(/type a sentence/i) as HTMLInputElement;
  const button = screen.getByTitle("Hear this voice speak your line") as HTMLButtonElement;
  return { input, button };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("CustomLinePreview component", () => {
  it("Enter in the input triggers playback with the typed (trimmed) text", async () => {
    apiFetchMock.mockResolvedValue(okAudioResponse());
    const { input } = renderPreview();

    fireEvent.change(input, { target: { value: "  Hello there.  " } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = apiFetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/studio\/tts$/);
    expect(JSON.parse(init.body)).toMatchObject({ text: "Hello there.", voice: "nova" });
    expect(currentAudio.playingId).toBe("custom:nova");
  });

  it("empty input never fires a request — Enter or click", async () => {
    apiFetchMock.mockResolvedValue(okAudioResponse());
    const { input, button } = renderPreview();

    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await act(async () => { fireEvent.click(button); });

    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(currentAudio.playingId).toBeNull();
  });

  it("whitespace-only input never fires a request", async () => {
    apiFetchMock.mockResolvedValue(okAudioResponse());
    const { input } = renderPreview();

    fireEvent.change(input, { target: { value: "   \t  " } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("play button is disabled while the line is empty, enabled once typed", () => {
    const { input, button } = renderPreview();

    expect(button.disabled).toBe(true);
    fireEvent.change(input, { target: { value: "A line" } });
    expect(button.disabled).toBe(false);
    fireEvent.change(input, { target: { value: "   " } }); // whitespace-only counts as empty
    expect(button.disabled).toBe(true);
  });

  it("play button is disabled while the request is in flight, then re-enables playing (pause stays clickable)", async () => {
    let resolveFetch!: (r: Response) => void;
    apiFetchMock.mockReturnValue(new Promise<Response>(res => { resolveFetch = res; }));
    const { input, button } = renderPreview();

    fireEvent.change(input, { target: { value: "Slow line" } });
    await act(async () => { fireEvent.click(button); });

    // In flight: loading — button disabled, nothing playing yet.
    expect(currentAudio.loadingId).toBe("custom:nova");
    expect(button.disabled).toBe(true);

    await act(async () => { resolveFetch(okAudioResponse()); });

    // Playing: button must stay enabled so the user can pause.
    expect(currentAudio.playingId).toBe("custom:nova");
    expect(button.disabled).toBe(false);
  });

  it("clicking while playing pauses without a new request — even after clearing the input", async () => {
    apiFetchMock.mockResolvedValue(okAudioResponse());
    const { input, button } = renderPreview();

    fireEvent.change(input, { target: { value: "Toggle me" } });
    await act(async () => { fireEvent.click(button); });
    expect(currentAudio.playingId).toBe("custom:nova");

    // Clearing the text must NOT disable the pause affordance.
    fireEvent.change(input, { target: { value: "" } });
    expect(button.disabled).toBe(false);

    await act(async () => { fireEvent.click(button); });
    expect(currentAudio.playingId).toBeNull();
    expect(pauseSpy).toHaveBeenCalled();
    expect(apiFetchMock).toHaveBeenCalledTimes(1); // pause is local, no refetch
  });

  it("input enforces the 200-character cap via maxLength", () => {
    const { input } = renderPreview();
    expect(input.maxLength).toBe(CUSTOM_LINE_MAX);
    expect(CUSTOM_LINE_MAX).toBe(200);
  });
});
