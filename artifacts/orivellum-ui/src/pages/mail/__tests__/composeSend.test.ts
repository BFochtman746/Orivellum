/**
 * Web compose — save-then-send resilience.
 *
 * Imports executeWebSendFlow directly from the production compose module so
 * any regression in the real 3-step chain will cause these tests to fail.
 *
 * Three scenarios:
 *   (a) PATCH returns 503        → nonce + send never called; error returned
 *   (b) PATCH ok, nonce fails    → send never called; error returned
 *   (c) Happy path               → all 3 steps run; success returned
 */
import { describe, it, expect, vi } from "vitest";

// Import the production function under test.  If this module fails to resolve,
// the test suite fails — enforcing coupling to the real implementation.
import {
  executeWebSendFlow,
  type WebSendFlowFetch,
  type WebSendFlowResponse,
} from "../compose";

// ── Helpers ───────────────────────────────────────────────────────────────────

function fakeOk(json: Record<string, unknown> = {}): WebSendFlowResponse {
  return {
    ok: true,
    statusText: "OK",
    json: () => Promise.resolve(json),
  };
}

function fakeErr(statusText: string): WebSendFlowResponse {
  return {
    ok: false,
    statusText,
    json: () => Promise.resolve({ detail: statusText }),
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

const ACTION_ID = "ar-web-001";
const RECORD_ID = "rec-web-001";
const BODY = "Thanks for getting in touch.";
const BASE = "/api";

describe("executeWebSendFlow — save-then-send chain", () => {
  // (a) ───────────────────────────────────────────────────────────────────────

  it("(a) PATCH 503 → nonce and send are never called", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>().mockResolvedValueOnce(
      fakeErr("Service Unavailable"),
    );

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    // Only 1 call was made — the PATCH
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0]![0]).toContain("/mail/drafts/");

    // Nonce and send endpoints were never reached
    expect(result.calledUrls.some(u => u.includes("send-nonce"))).toBe(false);
    expect(result.calledUrls.some(u => u.endsWith("/send"))).toBe(false);

    expect(result.success).toBe(false);
    expect(result.error).toMatch(/Draft save failed/);
  });

  // (b) ───────────────────────────────────────────────────────────────────────

  it("(b) PATCH ok, nonce endpoint 401 → send is never called", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>()
      .mockResolvedValueOnce(fakeOk({}))
      .mockResolvedValueOnce(fakeErr("Unauthorized"));

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch.mock.calls[1]![0]).toContain("send-nonce");

    const sendCalls = mockFetch.mock.calls.filter(([url]) =>
      url.endsWith("/send"),
    );
    expect(sendCalls).toHaveLength(0);

    expect(result.success).toBe(false);
    expect(result.error).toMatch(/Could not obtain send nonce/);
  });

  // (c) ───────────────────────────────────────────────────────────────────────

  it("(c) happy path — PATCH → nonce → send all succeed", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>()
      .mockResolvedValueOnce(fakeOk({}))
      .mockResolvedValueOnce(fakeOk({ nonce: "web-nonce-xyz" }))
      .mockResolvedValueOnce(fakeOk({ sent: true }));

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    expect(result.success).toBe(true);
    expect(result.error).toBeNull();

    // Exactly 3 calls in the correct order
    expect(mockFetch).toHaveBeenCalledTimes(3);
    expect(mockFetch.mock.calls[0]![0]).toContain("/mail/drafts/");
    expect(mockFetch.mock.calls[1]![0]).toContain("send-nonce");
    expect(mockFetch.mock.calls[2]![0]).toContain("/send");

    // Nonce from step 2 forwarded verbatim to step 3
    const sendBody = JSON.parse(
      (mockFetch.mock.calls[2]![1]?.body as string) ?? "{}",
    );
    expect(sendBody.nonce).toBe("web-nonce-xyz");
    expect(sendBody.action_request_id).toBe(ACTION_ID);
  });

  // Extra: send endpoint 500 → error returned, not swallowed
  it("send endpoint 500 → error is returned, not swallowed silently", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>()
      .mockResolvedValueOnce(fakeOk({}))
      .mockResolvedValueOnce(fakeOk({ nonce: "n3" }))
      .mockResolvedValueOnce(fakeErr("Internal Server Error"));

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    expect(result.success).toBe(false);
    // All 3 calls were made (error is at step 3, not suppressed earlier)
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  // Network-throw variants: fetch itself throws (dropped connection etc.)

  it("network error during PATCH → nonce and send never called", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>().mockRejectedValueOnce(
      new Error("Failed to fetch"),
    );

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(result.calledUrls.some(u => u.includes("send-nonce"))).toBe(false);
    expect(result.calledUrls.some(u => u.endsWith("/send"))).toBe(false);
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/fetch/i);
  });

  it("network error during nonce fetch → send never called", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>()
      .mockResolvedValueOnce(fakeOk({}))
      .mockRejectedValueOnce(new Error("Network error fetching nonce"));

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(result.calledUrls.some(u => u.endsWith("/send"))).toBe(false);
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/nonce/i);
  });

  it("nonce endpoint returns {} (missing nonce field) → send never called", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>()
      .mockResolvedValueOnce(fakeOk({}))
      .mockResolvedValueOnce(fakeOk({}));        // nonce absent

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(result.calledUrls.some(u => u.endsWith("/send"))).toBe(false);
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/invalid nonce/i);
  });

  it("nonce endpoint returns { nonce: null } → send never called", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>()
      .mockResolvedValueOnce(fakeOk({}))
      .mockResolvedValueOnce(fakeOk({ nonce: null }));

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    expect(result.success).toBe(false);
    expect(result.error).toMatch(/invalid nonce/i);
    expect(result.calledUrls.some(u => u.endsWith("/send"))).toBe(false);
  });

  it("nonce endpoint returns malformed JSON → send never called", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>()
      .mockResolvedValueOnce(fakeOk({}))
      .mockResolvedValueOnce({
        ok: true,
        statusText: "OK",
        json: () => Promise.reject(new Error("Unexpected token")),
      });

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    expect(result.calledUrls.some(u => u.endsWith("/send"))).toBe(false);
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/nonce response/i);
  });

  it("network error during send → error returned, not unhandled rejection", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>()
      .mockResolvedValueOnce(fakeOk({}))
      .mockResolvedValueOnce(fakeOk({ nonce: "n5" }))
      .mockRejectedValueOnce(new Error("Connection reset"));

    const result = await executeWebSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, BASE,
    );

    expect(mockFetch).toHaveBeenCalledTimes(3);
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/Connection reset/);
  });

  // Extra: body_text matches current editor state (not a stale snapshot)
  it("PATCH body contains the current editor text", async () => {
    const mockFetch = vi.fn<WebSendFlowFetch>()
      .mockResolvedValueOnce(fakeOk({}))
      .mockResolvedValueOnce(fakeOk({ nonce: "n4" }))
      .mockResolvedValueOnce(fakeOk({}));

    const edited = "Revised reply after reviewing the thread.";
    await executeWebSendFlow(ACTION_ID, RECORD_ID, edited, mockFetch, BASE);

    const patchBody = JSON.parse(
      (mockFetch.mock.calls[0]![1]?.body as string) ?? "{}",
    );
    expect(patchBody.body_text).toBe(edited);
  });
});
