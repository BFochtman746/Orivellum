/**
 * Flow 1 — Document upload → processing → knowledge items
 *
 * 1. Open the Library page.
 * 2. Upload a small text fixture via the Import dialog.
 * 3. Poll /api/library/{id} (unwrapping the nested `document` key) until
 *    readiness leaves "imported".
 * 4. Click the exact document card (matched by data-doc-id) in the Library
 *    list — uses Wouter's navigate(), avoids deep-link routing issues.
 * 5. Click the Knowledge tab and assert that at least one item with text
 *    from the fixture is rendered on screen.  An empty/pending state is a
 *    hard failure — extraction regressions must be caught here.
 */
import { test, expect } from '@playwright/test';
import path from 'node:path';
import { goto, ensureLoggedIn, pollUntil, API_ORIGIN } from '../helpers';

const FIXTURE = path.resolve('e2e/fixtures/sample.txt');

test('upload document → processing completes → knowledge items appear', async ({ page }) => {
  await goto(page, '/library');
  await ensureLoggedIn(page);

  // ── Open the Import dialog ────────────────────────────────────────────────
  // Trigger label changed to "Import Documents" (multi-file dialog)
  const importBtn = page.getByRole('button', { name: 'Import Documents' });
  await expect(importBtn).toBeVisible({ timeout: 10_000 });
  await importBtn.click();

  const fileInput = page.locator('input[type="file"]');
  await expect(fileInput).toBeAttached({ timeout: 5_000 });
  await fileInput.setInputFiles(FIXTURE, { force: true } as any);
  await page.waitForTimeout(500);

  // Intercept the upload response to capture the document ID
  const uploadResponsePromise = page.waitForResponse(
    (r) => r.url().includes('/api/library/upload') && r.request().method() === 'POST',
    { timeout: 30_000 },
  );
  // Submit button now reads "Import 1 file" (dynamic count label)
  const submitBtn = page.getByRole('button', { name: /^Import \d+ file/i }).last();
  await expect(submitBtn).toBeEnabled({ timeout: 5_000 });
  await submitBtn.click();

  const uploadResp = await uploadResponsePromise;
  expect(uploadResp.ok(), `Upload failed with status ${uploadResp.status()}`).toBeTruthy();
  const uploadData = await uploadResp.json();
  const docId: string = uploadData.document?.id;
  expect(docId, 'upload response must contain document.id').toBeTruthy();

  // ── Poll until readiness leaves "imported" ────────────────────────────────
  // GET /api/library/{id} wraps the doc: { document: { readiness, … } }
  await pollUntil<any>(
    page,
    `${API_ORIGIN}/api/library/${docId}`,
    (d) => {
      const r: string = d?.document?.readiness ?? '';
      return r !== '' && r !== 'imported';
    },
    { timeoutMs: 60_000, intervalMs: 3_000 },
  );

  // ── Navigate to the exact document card and click it ─────────────────────
  // Library cards carry data-doc-id={doc.id} so we can target them precisely
  // even when other documents (e.g. audiobooks) share part of the title.
  // Clicking calls Wouter's navigate('/library/:docId') — reliable in-app nav.
  await goto(page, '/library');
  const docCard = page.locator(`[data-doc-id="${docId}"]`);
  await expect(docCard).toBeVisible({ timeout: 15_000 });
  await docCard.click();
  await page.waitForLoadState('networkidle');
  await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10_000 });

  // ── Click the Knowledge tab ───────────────────────────────────────────────
  const knowledgeTabBtn = page.getByRole('button', { name: /knowledge/i }).first();
  await expect(knowledgeTabBtn).toBeVisible({ timeout: 10_000 });
  await knowledgeTabBtn.click();
  await page.waitForTimeout(2_000);

  // ── Assert at least one knowledge item is rendered ────────────────────────
  // The 158-word fixture always produces rule-based items containing phrases
  // from the text (e.g. "deep work", "cognitive load", "flow state").
  // An empty state means extraction regressed — fail hard so the CI catches it.
  const itemContent = page
    .getByText(/deep work|cognitive load|flow state|knowledge worker|time.?block/i)
    .first();
  await expect(itemContent).toBeVisible({ timeout: 10_000 });
});
