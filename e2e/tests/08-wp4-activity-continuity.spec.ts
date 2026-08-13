/**
 * Flow 8 — WP4 gate: truthful chat activity & continuity (mobile 390 × 844).
 *
 * 1. Mid-generation network drop → background → foreground → journal replay:
 *    the message is sent exactly once (no duplicate POST), and exactly one
 *    final assistant response is shown, labeled "Recovered response".
 * 2. Activity truthfulness: the strip renders ONLY server-emitted activity
 *    events; when the server emits none, no strip appears.
 * 3. Reasoning containment: raw thinking content is never displayed — only
 *    the factual "Reasoned privately" indicator.
 *
 * The AI engine is not available in CI, so the SSE stream and the journal
 * endpoints are mocked at the network layer (same pattern as flow 7's replay
 * test). Real-server exactly-once persistence of queued sends is covered by
 * flow 7 tests 1–3; this flow proves the client's mid-stream-drop contract.
 */
import { test, expect, type Page } from '@playwright/test';
import { ensureLoggedIn, API_ORIGIN, WEB_ORIGIN } from '../helpers';

const BASE_PATH = process.env.E2E_BASE_PATH ?? '';
const MOBILE = { width: 390, height: 844 };

function apiHeaders(): Record<string, string> {
  const key = process.env.E2E_API_KEY ?? process.env.SESSION_SECRET ?? '';
  return { 'X-Api-Key': key, 'Content-Type': 'application/json' };
}

async function createConversation(page: Page, title: string): Promise<string> {
  const r = await page.request.post(`${API_ORIGIN}/api/conversations`, {
    headers: apiHeaders(),
    data: { title },
  });
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  return body.conversation.id as string;
}

async function openConversation(page: Page, convId: string) {
  if (!page.url().startsWith(`${WEB_ORIGIN}${BASE_PATH}`)) {
    await page.goto(`${WEB_ORIGIN}${BASE_PATH}/`);
    await page.waitForLoadState('networkidle');
  }
  await ensureLoggedIn(page);
  await page.evaluate(
    ([base, id]) => {
      window.history.pushState({}, '', `${base}/chat?id=${id}`);
      window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
    },
    [BASE_PATH, convId] as [string, string],
  );
  await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });
}

/**
 * Install an in-page fetch wrapper that answers the chat-send POST with a
 * scripted SSE stream, counting attempts in window.__sendAttempts. The stream
 * emits the given frames then either errors (network drop) or ends cleanly.
 */
async function mockSendStream(
  page: Page,
  opts: { frames: Record<string, unknown>[]; endWith: 'drop' | 'done'; holdMs: number },
) {
  await page.addInitScript((o) => {
    (window as unknown as { __sendAttempts: number }).__sendAttempts = 0;
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
      if (method === 'POST' && /\/api\/conversations\/[^/]+\/messages$/.test(url.split('?')[0])) {
        (window as unknown as { __sendAttempts: number }).__sendAttempts += 1;
        const enc = new TextEncoder();
        const stream = new ReadableStream<Uint8Array>({
          start(c) {
            for (const frame of o.frames) {
              c.enqueue(enc.encode(`data: ${JSON.stringify(frame)}\n\n`));
            }
            setTimeout(() => {
              if (o.endWith === 'drop') {
                c.error(new TypeError('network connection lost'));
              } else {
                c.enqueue(enc.encode('data: [DONE]\n\n'));
                c.close();
              }
            }, o.holdMs);
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      return orig(input as RequestInfo, init);
    };
  }, opts);
}

test.describe('WP4 — truthful activity & continuity (mobile 390×844)', () => {
  test('mid-generation drop → background/foreground → replay: one send, one final response, no raw reasoning', async ({ page }) => {
    const convId = await createConversation(page, 'E2E WP4 — mid-stream drop replay');
    const jobId = `e2e-wp4-job-${Date.now()}`;
    const replyText = 'Final reply rebuilt from the journal after the drop.';
    const userText = `wp4 drop test ${Date.now()}`;
    const RAW_REASONING = 'SECRET-CHAIN-OF-THOUGHT-MUST-NEVER-RENDER';

    // Live stream: job id, server activity, a partial token — then the
    // connection drops mid-generation.
    await mockSendStream(page, {
      frames: [
        { job_id: jobId },
        { activity: { stage: 'retrieval', state: 'start', action: 'knowledge_search' } },
        { activity: { stage: 'retrieval', state: 'done', action: 'knowledge_search', source_count: 2, elapsed_ms: 120 } },
        { activity: { stage: 'generation', state: 'start' } },
        { token: 'Partial ' },
      ],
      endWith: 'drop',
      holdMs: 2_500,
    });

    // Journal replay: first poll still running, then the finished record.
    let eventCalls = 0;
    let replayFinished = false;
    await page.route(`**/api/conversations/jobs/${jobId}/events*`, (route) => {
      eventCalls += 1;
      const first = eventCalls === 1;
      if (!first) replayFinished = true;
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          first
            ? {
                job: { id: jobId, conversation_id: convId, message_id: null, state: 'running' },
                events: [
                  { seq: 1, kind: 'activity', payload: JSON.stringify({ activity: { stage: 'retrieval', state: 'start', action: 'knowledge_search' } }) },
                  { seq: 2, kind: 'activity', payload: JSON.stringify({ activity: { stage: 'retrieval', state: 'done', action: 'knowledge_search', source_count: 2, elapsed_ms: 120 } }) },
                  { seq: 3, kind: 'activity', payload: JSON.stringify({ activity: { stage: 'generation', state: 'start' } }) },
                ],
              }
            : {
                job: { id: jobId, conversation_id: convId, message_id: 'srv-wp4-msg', state: 'done' },
                events: [
                  { seq: 4, kind: 'thinking', payload: JSON.stringify({ thinking: RAW_REASONING }) },
                  { seq: 5, kind: 'chunk', payload: JSON.stringify({ token: replyText }) },
                  { seq: 6, kind: 'activity', payload: JSON.stringify({ activity: { stage: 'generation', state: 'done', elapsed_ms: 900 } }) },
                  { seq: 7, kind: 'meta', payload: JSON.stringify({ message_id: 'srv-wp4-msg' }) },
                  { seq: 8, kind: 'done', payload: '' },
                ],
              },
        ),
      });
    });

    // Once replay completes, the server conversation holds exactly one user
    // row and one assistant row (journal→DB persistence is covered by the
    // backend suite; this mock represents that persisted state).
    await page.route(`**/api/conversations/${convId}`, async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      if (replayFinished) {
        body.messages = [
          ...(body.messages ?? []),
          { id: 'srv-wp4-user', role: 'user', text: userText, created_at: new Date().toISOString(), meta: {} },
          { id: 'srv-wp4-msg', role: 'assistant', text: replyText, created_at: new Date().toISOString(), meta: { thinking: RAW_REASONING } },
        ];
      }
      await route.fulfill({ response: res, json: body });
    });

    await page.goto(`${WEB_ORIGIN}${BASE_PATH}/`);
    await ensureLoggedIn(page);
    await openConversation(page, convId);
    await page.setViewportSize(MOBILE);

    await page.locator('textarea').fill(userText);
    await page.keyboard.press('Enter');

    // Activity strip appears ONLY from the server-emitted events, with the
    // truthful retrieval label.
    await expect(page.getByTestId('activity-strip')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('activity-strip')).toContainText(/knowledge|Writing/i);

    // The stream drops (~2.5 s in). Simulate iOS background → foreground —
    // recovery must fire on the visibility/online triggers.
    await page.waitForTimeout(3_000);
    await page.evaluate(() => {
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('online'));
    });

    // Exactly one final assistant response, labeled as recovered.
    await expect(page.getByText(replyText)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('badge-recovered').first()).toBeVisible();
    expect(await page.getByText(replyText).count()).toBe(1);

    // Exactly one user message — never re-sent after the drop.
    expect(await page.getByText(userText).count()).toBe(1);
    expect(
      await page.evaluate(() => (window as unknown as { __sendAttempts: number }).__sendAttempts),
    ).toBe(1);

    // Raw chain-of-thought never renders; only the factual indicator does.
    await expect(page.getByText(RAW_REASONING)).toHaveCount(0);
    await expect(page.getByTestId('reasoning-indicator').first()).toBeVisible();
  });

  test('no server activity events → no activity strip (client never invents steps)', async ({ page }) => {
    const convId = await createConversation(page, 'E2E WP4 — no invented steps');
    const jobId = `e2e-wp4-clean-${Date.now()}`;

    await mockSendStream(page, {
      frames: [{ job_id: jobId }, { token: 'Hello from a stream with no activity events.' }],
      endWith: 'done',
      holdMs: 1_200,
    });

    await page.goto(`${WEB_ORIGIN}${BASE_PATH}/`);
    await ensureLoggedIn(page);
    await openConversation(page, convId);
    await page.setViewportSize(MOBILE);

    await page.locator('textarea').fill('activity truthfulness check');
    await page.keyboard.press('Enter');

    // The reply streams in…
    await expect(page.getByText('Hello from a stream with no activity events.')).toBeVisible({
      timeout: 15_000,
    });
    // …but the strip never appeared: no server events, no steps. (Checked
    // after the reply is fully visible, i.e. after the whole stream window.)
    await expect(page.getByTestId('activity-strip')).toHaveCount(0);
  });
});
