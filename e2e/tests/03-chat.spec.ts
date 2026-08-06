/**
 * Flow 3 — Create conversation → send message → verify AI response
 *
 * 1. Navigate to the Chat page.
 * 2. Create a new conversation (or use the first available).
 * 3. Send a short, deterministic prompt.
 * 4. Assert that a new assistant bubble (data-role="assistant") appears with
 *    non-empty text that is NOT the user's own message — proving a real AI
 *    reply was streamed, not just the user's prompt re-rendered.
 *
 * If the AI server is unavailable the test is skipped rather than failed.
 */
import { test, expect } from '@playwright/test';
import { goto, ensureLoggedIn, API_ORIGIN } from '../helpers';

const USER_PROMPT = 'Reply with exactly the word "OK".';

test('chat — send message → AI response streams within 30 s', async ({ page }) => {
  // ── Preflight: check AI server availability ───────────────────────────────
  const healthResp = await page.request.get(`${API_ORIGIN}/api/system/health`);
  if (healthResp.ok()) {
    const health = await healthResp.json();
    if (health?.services?.ai?.status === 'unavailable') {
      test.skip(true, 'AI server unavailable — skipping chat test');
      return;
    }
  }

  await goto(page, '/chat');
  await ensureLoggedIn(page);

  // ── Create a new conversation ─────────────────────────────────────────────
  const newConvBtn = page
    .getByRole('button', { name: /new conversation|new chat|\+ chat/i })
    .first();
  if (await newConvBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await newConvBtn.click();
    await page.waitForTimeout(1_000);
  }

  // ── Record how many assistant bubbles already exist ───────────────────────
  // Each message div carries data-role="user" or data-role="assistant" (added
  // to chat/index.tsx so tests can target role-specific elements reliably).
  const existingAssistantCount = await page
    .locator('[data-role="assistant"]')
    .count();

  // ── Find the message textarea and send the prompt ─────────────────────────
  const textarea = page
    .getByRole('textbox', { name: /message|chat|ask/i })
    .or(page.locator('textarea').first());
  await expect(textarea).toBeVisible({ timeout: 10_000 });
  await textarea.fill(USER_PROMPT);
  await textarea.press('Enter');

  // ── Wait for a NEW assistant bubble to appear ─────────────────────────────
  // Playwright's nth(n) is 0-based. After sending, the page should have at
  // least existingAssistantCount+1 assistant bubbles.
  const newAssistantBubble = page
    .locator('[data-role="assistant"]')
    .nth(existingAssistantCount);

  await expect(newAssistantBubble).toBeVisible({ timeout: 30_000 });

  // ── Verify the response text is non-empty and is NOT the user's prompt ────
  // This guards against the locator accidentally matching the user's own text.
  const responseText = await newAssistantBubble.innerText({ timeout: 5_000 });
  expect(responseText.trim().length, 'assistant bubble must contain text').toBeGreaterThan(0);
  expect(responseText, 'assistant bubble must not echo the user prompt').not.toMatch(
    /reply with exactly the word/i,
  );
});
