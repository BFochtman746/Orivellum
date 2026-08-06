/**
 * Flow 5 — Import duplicate → dedup detection fires
 *
 * 1. Upload the sample fixture via the API to ensure it's in the library.
 * 2. Upload the exact same file again via the UI Import dialog.
 * 3. Assert the upload API response contains `duplicate: true` (SHA-256 match).
 * 4. Assert the UI navigated to the existing document's detail page.
 */
import { test, expect } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import { goto, ensureLoggedIn, API_ORIGIN, WEB_ORIGIN, BASE_PATH } from '../helpers';

const FIXTURE = path.resolve('e2e/fixtures/sample.txt');

test('duplicate upload → server returns duplicate:true and UI navigates to existing doc', async ({ page }) => {
  // ── First upload via API — ensure the fixture is in the library ───────────
  const fileBuffer = fs.readFileSync(FIXTURE);
  const firstResp = await page.request.post(`${API_ORIGIN}/api/library/upload`, {
    multipart: {
      file: { name: 'sample.txt', mimeType: 'text/plain', buffer: fileBuffer },
    },
  });
  expect(firstResp.ok(), 'first API upload should succeed').toBeTruthy();
  const firstData = await firstResp.json();
  // The existing doc ID — either newly created or already existed
  const existingDocId: string = firstData.document?.id;
  expect(existingDocId, 'first upload must return a document id').toBeTruthy();

  // ── Navigate to Library for the second (UI) upload ────────────────────────
  await goto(page, '/library');
  await ensureLoggedIn(page);

  // Intercept the upload response BEFORE clicking submit so we catch it
  // even if the page navigates away immediately afterwards.
  const uploadResponsePromise = page.waitForResponse(
    (r) => r.url().includes('/api/library/upload') && r.request().method() === 'POST',
    { timeout: 30_000 },
  );

  // Click "Import Document" to open the dialog
  const importBtn = page.getByRole('button', { name: 'Import Document' });
  await expect(importBtn).toBeVisible({ timeout: 10_000 });
  await importBtn.click();

  // Set files on the hidden file input
  const fileInput = page.locator('input[type="file"]');
  await expect(fileInput).toBeAttached({ timeout: 5_000 });
  await fileInput.setInputFiles(FIXTURE, { force: true } as any);
  await page.waitForTimeout(500);

  // Click the Import submit button
  const submitBtn = page.getByRole('button', { name: 'Import' }).last();
  await expect(submitBtn).toBeEnabled({ timeout: 5_000 });
  await submitBtn.click();

  // ── Assert duplicate detection via API response (hard assertion) ──────────
  // The server detects the SHA-256 match and returns { duplicate: true, document: {...} }
  const uploadResp = await uploadResponsePromise;
  expect(uploadResp.ok(), 'second upload request must succeed').toBeTruthy();
  const uploadData = await uploadResp.json();

  expect(
    uploadData.duplicate,
    `Expected duplicate:true in upload response. Got: ${JSON.stringify(uploadData).slice(0, 200)}`,
  ).toBe(true);

  // The returned document must be the same one from the first upload
  expect(uploadData.document?.id).toBe(existingDocId);

  // ── Assert UI navigated to the existing document's detail page ────────────
  // After a duplicate upload the frontend navigates to /library/:docId.
  // This is a hard assertion — navigation is a core part of the dedup UX.
  await page.waitForURL(
    (url) => url.pathname.endsWith(`/library/${existingDocId}`),
    { timeout: 15_000 },
  );

  // Confirm the detail page rendered (shows the document title)
  await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10_000 });
});
