/**
 * Mail Steward — compose send-flow resilience.
 *
 * Imports executeSendFlow directly from the production compose module so any
 * regression in the real 3-step chain will cause these tests to fail.
 *
 * The 3-step chain enforced by executeSendFlow:
 *   Step 1 — PATCH /mail/drafts/:actionRequestId  (persist latest edits)
 *   Step 2 — POST  /mail/decisions/:recordId/send-nonce (fresh single-use token)
 *   Step 3 — POST  /mail/decisions/:recordId/send (deliver)
 *
 * Scenarios covered:
 *   (a) PATCH returns 503        → nonce + send never called; error returned
 *   (b) PATCH ok, nonce throws   → send never called; error returned
 *   (c) Happy path               → all 3 steps run; nonce forwarded; success
 *
 * Additionally:
 *   Body text in PATCH matches current editor content (not stale)
 *   null body_text is serialised correctly
 *   Each send attempt fetches its own nonce (freshness invariant)
 */

// Import the production function under test.  If the import fails (e.g. the
// function was moved or renamed), the test suite fails — enforcing coupling.
// The utility module is intentionally separated from the RN component so the
// pure send-chain logic can be tested without pulling in React Native.
import {
  executeSendFlow,
  type SendFlowFetch,
  type SendFlowResult,
} from '../lib/mail-send-flow';

// ── Constants ─────────────────────────────────────────────────────────────────

const API       = 'https://localhost:8000/api';
const ACTION_ID = 'ar-mobile-001';
const RECORD_ID = 'rec-mobile-001';
const BODY      = 'Thank you for your message.';

// ── Suite ─────────────────────────────────────────────────────────────────────

describe('executeSendFlow — save-then-send chain (mobile)', () => {
  // (a) ───────────────────────────────────────────────────────────────────────

  it('(a) PATCH 503 → nonce and send are never called', async () => {
    const mockFetch: jest.MockedFunction<SendFlowFetch> = jest.fn()
      .mockRejectedValueOnce(new Error('503 Service Unavailable'));

    const result: SendFlowResult = await executeSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, API,
    );

    // Only 1 HTTP call — the PATCH
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0]![0]).toContain('/mail/drafts/');

    // Nonce and send endpoints were never reached
    expect(result.calledUrls.some(u => u.includes('send-nonce'))).toBe(false);
    expect(result.calledUrls.some(u => u.endsWith('/send'))).toBe(false);

    expect(result.success).toBe(false);
    expect(result.error).toMatch(/503/);
  });

  // (b) ───────────────────────────────────────────────────────────────────────

  it('(b) PATCH ok, nonce endpoint throws → send is never called', async () => {
    const mockFetch: jest.MockedFunction<SendFlowFetch> = jest.fn()
      .mockResolvedValueOnce({ updated: true })
      .mockRejectedValueOnce(new Error('Network error fetching send-nonce'));

    const result: SendFlowResult = await executeSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, API,
    );

    // 2 calls: PATCH + nonce attempt
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch.mock.calls[1]![0]).toContain('send-nonce');

    // Send endpoint was never reached
    const sendCalls = mockFetch.mock.calls.filter(
      ([url]) => (url as string).endsWith('/send'),
    );
    expect(sendCalls).toHaveLength(0);

    expect(result.success).toBe(false);
    expect(result.error).toMatch(/send-nonce/i);
  });

  // (c) ───────────────────────────────────────────────────────────────────────

  it('(c) happy path — PATCH → nonce → send all succeed', async () => {
    const mockFetch: jest.MockedFunction<SendFlowFetch> = jest.fn()
      .mockResolvedValueOnce({ updated: true })
      .mockResolvedValueOnce({ nonce: 'nonce-abc-123' })
      .mockResolvedValueOnce({ sent: true });

    const result: SendFlowResult = await executeSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, API,
    );

    expect(result.success).toBe(true);
    expect(result.error).toBeNull();

    // Exactly 3 calls in the correct order
    expect(mockFetch).toHaveBeenCalledTimes(3);
    expect(mockFetch.mock.calls[0]![0]).toContain('/mail/drafts/');
    expect(mockFetch.mock.calls[1]![0]).toContain('send-nonce');
    expect(mockFetch.mock.calls[2]![0]).toContain('/send');

    // The nonce from step 2 was forwarded verbatim to step 3
    const sendBody = JSON.parse(
      (mockFetch.mock.calls[2]![1]?.body as string) ?? '{}',
    );
    expect(sendBody.nonce).toBe('nonce-abc-123');
    expect(sendBody.action_request_id).toBe(ACTION_ID);
  });

  // Extra: current editor text (not stale content) is persisted in PATCH
  it('PATCH body contains the current editor text, not stale content', async () => {
    const mockFetch: jest.MockedFunction<SendFlowFetch> = jest.fn()
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ nonce: 'n1' })
      .mockResolvedValueOnce({});

    const edited = 'Edited text — not the original suggestion.';
    await executeSendFlow(ACTION_ID, RECORD_ID, edited, mockFetch, API);

    const patchBody = JSON.parse(
      (mockFetch.mock.calls[0]![1]?.body as string) ?? '{}',
    );
    expect(patchBody.body_text).toBe(edited);
  });

  // Extra: null body_text serialised correctly when user clears the editor
  it('PATCH body_text is null when the user clears the editor', async () => {
    const mockFetch: jest.MockedFunction<SendFlowFetch> = jest.fn()
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ nonce: 'n2' })
      .mockResolvedValueOnce({});

    await executeSendFlow(ACTION_ID, RECORD_ID, null, mockFetch, API);

    const patchBody = JSON.parse(
      (mockFetch.mock.calls[0]![1]?.body as string) ?? '{}',
    );
    expect(patchBody.body_text).toBeNull();
  });
});

  // Nonce shape validation: missing or null nonce → send never called
  it('nonce endpoint returns {} (missing nonce) → send is never called', async () => {
    const mockFetch: jest.MockedFunction<SendFlowFetch> = jest.fn()
      .mockResolvedValueOnce({ updated: true })
      .mockResolvedValueOnce({});               // nonce absent in response body

    const result: SendFlowResult = await executeSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, API,
    );

    expect(mockFetch).toHaveBeenCalledTimes(2);
    const sendCalls = mockFetch.mock.calls.filter(
      ([url]) => (url as string).endsWith('/send'),
    );
    expect(sendCalls).toHaveLength(0);
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/invalid nonce/i);
  });

  it('nonce endpoint returns { nonce: null } → send is never called', async () => {
    const mockFetch: jest.MockedFunction<SendFlowFetch> = jest.fn()
      .mockResolvedValueOnce({ updated: true })
      .mockResolvedValueOnce({ nonce: null });

    const result: SendFlowResult = await executeSendFlow(
      ACTION_ID, RECORD_ID, BODY, mockFetch, API,
    );

    expect(result.success).toBe(false);
    expect(result.error).toMatch(/invalid nonce/i);
    expect(result.calledUrls.some(u => u.endsWith('/send'))).toBe(false);
  });

// ── Nonce freshness invariant ─────────────────────────────────────────────────
//
// Because the nonce is fetched fresh in step 2 on every call, consecutive send
// attempts each obtain their own single-use token. This test uses the real
// executeSendFlow to confirm the structural guarantee is preserved.

describe('executeSendFlow — nonce freshness invariant', () => {
  it('each send attempt fetches its own nonce, never reusing an old one', async () => {
    let nonceCounter = 0;
    const nonces = ['first-nonce', 'second-nonce'];

    const mockFetch: jest.MockedFunction<SendFlowFetch> = jest.fn()
      .mockImplementation(async (url: string) => {
        if (url.includes('/mail/drafts/'))  return {};
        if (url.includes('send-nonce'))     return { nonce: nonces[nonceCounter++] };
        if (url.endsWith('/send'))          return {};
        return {};
      });

    // Two consecutive send attempts
    const r1 = await executeSendFlow('ar-1', 'rec-1', 'hello', mockFetch, API);
    const r2 = await executeSendFlow('ar-1', 'rec-1', 'hello', mockFetch, API);

    expect(r1.success).toBe(true);
    expect(r2.success).toBe(true);

    // Each attempt issued its own nonce request
    const nonceCalls = mockFetch.mock.calls.filter(
      ([url]) => (url as string).includes('send-nonce'),
    );
    expect(nonceCalls).toHaveLength(2);
  });
});
