/**
 * Mail Steward — compose send-flow utility.
 *
 * Extracted from the compose screen into a standalone module so this pure
 * TypeScript logic can be unit-tested without pulling in any React Native
 * dependencies.
 *
 * Imported by:
 *   artifacts/mobile/app/mail/compose/[actionRequestId].tsx  (production)
 *   artifacts/mobile/__tests__/mailComposeSend.test.ts        (tests)
 */

/** fetch-compatible signature that throws on non-2xx (mobileFetchJson contract). */
export type SendFlowFetch = (url: string, opts?: RequestInit) => Promise<unknown>;

export interface SendFlowResult {
  success: boolean;
  error: string | null;
  /** Every URL called, in order. Lets tests assert absent calls. */
  calledUrls: string[];
}

/**
 * Executes the ordered 3-step send chain:
 *   1. PATCH /mail/drafts/:actionRequestId  — persist current editor text
 *   2. POST  /mail/decisions/:recordId/send-nonce — fresh single-use token
 *   3. POST  /mail/decisions/:recordId/send       — deliver
 *
 * A failure at any step returns `{ success: false }` and aborts the remaining
 * steps, so a stale draft is never delivered on a partial network failure.
 *
 * @param fetchFn  Must throw on any non-2xx response (mobileFetchJson contract).
 */
export async function executeSendFlow(
  actionRequestId: string,
  recordId: string,
  bodyText: string | null,
  fetchFn: SendFlowFetch,
  api: string,
): Promise<SendFlowResult> {
  const calledUrls: string[] = [];

  // Step 1 — persist latest edits before sending
  try {
    const url = `${api}/mail/drafts/${actionRequestId}`;
    calledUrls.push(url);
    await fetchFn(url, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body_text: bodyText }),
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'Save failed';
    return { success: false, error: msg, calledUrls };
  }

  // Step 2 — single-use nonce, fetched fresh (never stored in URL/state)
  let nonce: string;
  try {
    const url = `${api}/mail/decisions/${recordId}/send-nonce`;
    calledUrls.push(url);
    const result = (await fetchFn(url, { method: 'POST' })) as { nonce?: unknown };
    if (typeof result.nonce !== 'string' || result.nonce.length === 0) {
      return { success: false, error: 'Server returned an invalid nonce', calledUrls };
    }
    nonce = result.nonce;
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'Nonce failed';
    return { success: false, error: msg, calledUrls };
  }

  // Step 3 — deliver
  try {
    const url = `${api}/mail/decisions/${recordId}/send`;
    calledUrls.push(url);
    await fetchFn(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action_request_id: actionRequestId, nonce }),
    });
    return { success: true, error: null, calledUrls };
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'Send failed';
    return { success: false, error: msg, calledUrls };
  }
}
