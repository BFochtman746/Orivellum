/**
 * Flow 7 — iPhone continuity core, at mobile viewport (390 × 844).
 *
 * 1. Offline send: a message written in a dead zone becomes a "queued"
 *    bubble (never lost), and delivers automatically when connectivity
 *    returns.
 * 2. Reload with a queue: the queued op survives a full page reload
 *    (IndexedDB persistence) — the sync chip reports the queue, and the op
 *    delivers once the API is reachable again.
 * 3. Replay on launch: a pending generation job recorded before a
 *    kill/suspend is replayed from the server journal into a bubble
 *    labeled "Recovered response".
 *
 * Navigation happens at desktop size (the sidebar-driven goto() helper),
 * then the viewport is switched to 390 × 844 before the interactions under
 * test — the continuity behaviors themselves are what must work on mobile.
 */
import { test, expect, type Page } from '@playwright/test';
import { ensureLoggedIn, API_ORIGIN, WEB_ORIGIN } from '../helpers';

// The dev server serves the SPA at the root path (BASE_PATH env defaults are
// stripped by the proxy) — helpers.BASE_PATH predates this.
const BASE_PATH = process.env.E2E_BASE_PATH ?? '';

const MOBILE = { width: 390, height: 844 };

function apiHeaders(): Record<string, string> {
  const key = process.env.E2E_API_KEY ?? process.env.SESSION_SECRET ?? '';
  return { 'X-Api-Key': key, 'Content-Type': 'application/json' };
}

/** Create a conversation server-side and return its id. */
async function createConversation(page: Page, title: string): Promise<string> {
  const r = await page.request.post(`${API_ORIGIN}/api/conversations`, {
    headers: apiHeaders(),
    data: { title },
  });
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  return body.conversation.id as string;
}

/** Open /chat then select the conversation via the ?id= search param.
 *
 * The sidebar-driven goto() helper predates the Home-Screen shell redesign,
 * so navigate to root (always works) and then push the chat route in-app. */
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

test.describe('continuity core (mobile 390×844)', () => {
  test('offline send → queued bubble → auto-delivers on reconnect', async ({ page, context }) => {
    const convId = await createConversation(page, 'E2E continuity — offline send');
    await openConversation(page, convId);
    await page.setViewportSize(MOBILE);

    // Go into a dead zone and send.
    await context.setOffline(true);
    const text = `offline message ${Date.now()}`;
    await page.locator('textarea').fill(text);
    await page.keyboard.press('Enter');

    // The message is preserved as a queued bubble — not lost, not "failed".
    await expect(page.getByTestId('status-queued').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(text)).toBeVisible();

    // Connectivity returns → the outbox flushes without any user action.
    await context.setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await expect(page.getByTestId('status-queued')).toHaveCount(0, { timeout: 30_000 });

    // Delivered for real: the server now holds the message.
    await expect
      .poll(
        async () => {
          const r = await page.request.get(`${API_ORIGIN}/api/conversations/${convId}`, {
            headers: apiHeaders(),
          });
          const body = await r.json();
          const msgs = (body.messages ?? []) as Array<{ role: string; text: string }>;
          return msgs.some((m) => m.role === 'user' && m.text === text);
        },
        { timeout: 20_000 },
      )
      .toBe(true);
  });

  test('queued op survives reload and delivers when the API is reachable again', async ({ page, context }) => {
    const convId = await createConversation(page, 'E2E continuity — reload queue');
    await openConversation(page, convId);
    await page.setViewportSize(MOBILE);

    // Queue a message offline.
    await context.setOffline(true);
    const text = `reload-survivor ${Date.now()}`;
    await page.locator('textarea').fill(text);
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('status-queued').first()).toBeVisible({ timeout: 10_000 });

    // Back online for the page load itself, but keep the message POST failing
    // (server "unreachable") so the op must stay queued across the reload.
    await context.setOffline(false);
    await context.route('**/api/conversations/*/messages', (route) => route.abort('connectionfailed'));

    await page.reload();
    await ensureLoggedIn(page);

    // IndexedDB kept the op: the sync status reports a queue after reload.
    await expect
      .poll(
        async () =>
          page
            .locator('[data-testid="sync-status-chip"][data-sync-state="queued"]')
            .count(),
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0);

    // Server reachable again → flush delivers the op exactly once.
    await context.unroute('**/api/conversations/*/messages');
    await page.evaluate(() => window.dispatchEvent(new Event('online')));

    await expect
      .poll(
        async () => {
          const r = await page.request.get(`${API_ORIGIN}/api/conversations/${convId}`, {
            headers: apiHeaders(),
          });
          const body = await r.json();
          const msgs = (body.messages ?? []) as Array<{ role: string; text: string }>;
          return msgs.filter((m) => m.role === 'user' && m.text === text).length;
        },
        { timeout: 45_000 },
      )
      .toBe(1); // exactly once — client_msg_id idempotency
  });

  test('503 on the initial send → queued (not failed) → delivers when the server is back', async ({ page, context }) => {
    const convId = await createConversation(page, 'E2E continuity — 503 send');
    await openConversation(page, convId);
    await page.setViewportSize(MOBILE);

    // The server answers but is temporarily broken (restart window).
    await context.route('**/api/conversations/*/messages', (route) =>
      route.fulfill({ status: 503, contentType: 'text/plain', body: 'restarting' }),
    );

    const text = `restart-survivor ${Date.now()}`;
    await page.locator('textarea').fill(text);
    await page.keyboard.press('Enter');

    // Transient server error must land in queued — NOT the terminal failed
    // state — so the flusher retries without user action.
    await expect(page.getByTestId('status-queued').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Not delivered')).toHaveCount(0);

    // Server comes back → flush delivers exactly once.
    await context.unroute('**/api/conversations/*/messages');
    await page.evaluate(() => window.dispatchEvent(new Event('online')));

    await expect
      .poll(
        async () => {
          const r = await page.request.get(`${API_ORIGIN}/api/conversations/${convId}`, {
            headers: apiHeaders(),
          });
          const body = await r.json();
          const msgs = (body.messages ?? []) as Array<{ role: string; text: string }>;
          return msgs.filter((m) => m.role === 'user' && m.text === text).length;
        },
        { timeout: 45_000 },
      )
      .toBe(1);
  });

  test('IndexedDB write failure while offline → honest failure, never a fake "queued"', async ({ page, context }) => {
    const convId = await createConversation(page, 'E2E continuity — storage failure');

    // Break IndexedDB before the app boots (private-mode / storage-eviction
    // simulation): every open() request errors out.
    await page.addInitScript(() => {
      const broken = {
        open() {
          const req: Record<string, unknown> = { error: new DOMException('QuotaExceededError') };
          setTimeout(() => {
            (req.onerror as ((ev: unknown) => void) | undefined)?.({ target: req });
          }, 0);
          return req;
        },
        deleteDatabase() {
          return { onsuccess: null, onerror: null };
        },
      };
      Object.defineProperty(window, 'indexedDB', { value: broken, configurable: true });
    });

    await page.goto(`${WEB_ORIGIN}${BASE_PATH}/`);
    await ensureLoggedIn(page);
    await openConversation(page, convId);
    await page.setViewportSize(MOBILE);

    await context.setOffline(true);
    const text = `unsaveable ${Date.now()}`;
    await page.locator('textarea').fill(text);
    await page.keyboard.press('Enter');

    // Nothing durable exists — the UI must say so, not claim it is queued.
    await expect(page.getByText('Not delivered')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('status-queued')).toHaveCount(0);
    await context.setOffline(false);
  });

  test('pending generation replays from the journal as a "Recovered response"', async ({ page }) => {
    const convId = await createConversation(page, 'E2E continuity — replay');
    const jobId = `e2e-job-${Date.now()}`;
    const replyText = 'This reply was rebuilt from the journal.';

    // After replay finishes, the client hands the bubble back to the
    // refetched server rows — so the conversation GET must return the
    // recovered assistant row (the journal→DB write is covered by the
    // backend suite; here we prove the client replay path).
    await page.route(`**/api/conversations/${convId}`, async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.messages = [
        ...(body.messages ?? []),
        {
          id: 'srv-msg-1',
          role: 'assistant',
          text: replyText,
          created_at: new Date().toISOString(),
          meta: {},
        },
      ];
      await route.fulfill({ response: res, json: body });
    });
    await page.route(`**/api/conversations/jobs/${jobId}/events*`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          job: { id: jobId, conversation_id: convId, message_id: 'srv-msg-1', state: 'done' },
          events: [
            { seq: 1, kind: 'chunk', payload: JSON.stringify({ token: replyText }) },
            { seq: 2, kind: 'meta', payload: JSON.stringify({ message_id: 'srv-msg-1' }) },
            { seq: 3, kind: 'done', payload: '' },
          ],
        }),
      }),
    );

    // Simulate "app was killed mid-generation": the pending-gen record exists
    // in localStorage before the app boots.
    await page.addInitScript(
      ([id, cid]) => {
        localStorage.setItem(
          'oriv-pending-gen',
          JSON.stringify({ jobId: id, convId: cid, startedAt: Date.now() }),
        );
      },
      [jobId, convId] as [string, string],
    );

    await page.goto(`${WEB_ORIGIN}${BASE_PATH}/`);
    await ensureLoggedIn(page);
    await openConversation(page, convId);
    await page.setViewportSize(MOBILE);

    // The reply is rebuilt exactly once and labeled as recovered.
    await expect(page.getByText(replyText)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('badge-recovered').first()).toBeVisible();
    expect(await page.getByText(replyText).count()).toBe(1);
  });
});
