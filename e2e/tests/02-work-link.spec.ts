/**
 * Flow 2 — Create Work → link document via UI → verify stats update
 *
 * 1. Navigate to the Works page and create a new Work via the "New Work" dialog.
 * 2. Navigate to Library, click an unlinked document card (Wouter navigate).
 * 3. Use the "Work" Select in the Overview tab to assign the new Work.
 * 4. Poll the Work stats endpoint until documents_by_kind total ≥ 1.
 */
import { test, expect } from '@playwright/test';
import { goto, ensureLoggedIn, pollUntil, API_ORIGIN } from '../helpers';

const WORK_TITLE = `E2E Work ${Date.now()}`;

test('create Work → link document via UI → stats reflect linked document', async ({ page }) => {
  // ── 1. Navigate to Works and create a new Work ────────────────────────────
  await goto(page, '/works');
  await ensureLoggedIn(page);

  const newWorkBtn = page.getByRole('button', { name: 'New Work' }).first();
  await expect(newWorkBtn).toBeVisible({ timeout: 10_000 });
  await newWorkBtn.click();

  const titleInput = page.getByPlaceholder(/architecture of memory/i);
  await expect(titleInput).toBeVisible({ timeout: 5_000 });
  await titleInput.fill(WORK_TITLE);

  const createBtn = page.getByRole('button', { name: 'Create Work' });
  await expect(createBtn).toBeEnabled({ timeout: 3_000 });
  await createBtn.click();
  await page.waitForTimeout(2_000);

  // ── Fetch the new Work's ID from the API ──────────────────────────────────
  const worksResp = await page.request.get(`${API_ORIGIN}/api/works?limit=20`);
  expect(worksResp.ok()).toBeTruthy();
  const worksData = await worksResp.json();
  const newWork = (worksData.works ?? []).find((w: any) => w.title === WORK_TITLE);
  expect(newWork, `expected work "${WORK_TITLE}" in API response`).toBeTruthy();
  const workId: string = newWork.id;

  // ── 2. Find an unlinked, ready document in the library ────────────────────
  const libResp = await page.request.get(`${API_ORIGIN}/api/library?limit=20`);
  expect(libResp.ok()).toBeTruthy();
  const libData = await libResp.json();
  const docs: any[] = libData.documents ?? libData.items ?? [];
  const doc =
    docs.find((d: any) => !d.work_id && d.readiness === 'ready') ??
    docs.find((d: any) => !d.work_id) ??
    docs[0];
  expect(doc, 'expected at least one document in the library').toBeTruthy();
  const docTitle: string = doc.title ?? doc.source?.split('/').pop() ?? 'Untitled';
  const workId2 = doc.work_id; // record if it's already linked (for assertion later)

  // ── 3. Navigate to Library and click the document card ───────────────────
  // Cards use onClick → Wouter navigate('/library/:docId'), which is reliable.
  await goto(page, '/library');
  await ensureLoggedIn(page);

  const docCardHeading = page.locator('h3').filter({ hasText: docTitle }).first();
  await expect(docCardHeading).toBeVisible({ timeout: 15_000 });
  await docCardHeading.click();
  await page.waitForLoadState('networkidle');

  // Wait for the detail page to render
  await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10_000 });

  // ── 4. Use the Work Select in the Overview tab to link the Work ───────────
  // The Overview tab is active by default. The Select trigger shows the
  // current work title or "Unlinked" (when work_id is null).
  // The trigger renders as role="combobox".
  const workSelectTrigger = page
    .getByRole('combobox')
    .filter({ hasText: /unlinked/i })
    .or(
      // If the doc was already linked to something else, pick the trigger anyway
      page.locator('div').filter({ hasText: /^\s*(⇌ )?Work\s*$/ }).getByRole('combobox'),
    )
    .first();
  await expect(workSelectTrigger).toBeVisible({ timeout: 10_000 });
  await workSelectTrigger.click();

  // Wait for the Radix SelectContent listbox, then click our work
  const workOption = page.getByRole('option', { name: new RegExp(WORK_TITLE, 'i') });
  await expect(workOption).toBeVisible({ timeout: 5_000 });
  await workOption.click();

  // Wait for the mutation to settle — trigger text changes to the work title
  await expect(
    page.getByRole('combobox').filter({ hasText: new RegExp(WORK_TITLE, 'i') }),
  ).toBeVisible({ timeout: 10_000 });

  // ── 5. Poll Work stats until at least one document appears ───────────────
  const stats = await pollUntil<any>(
    page,
    `${API_ORIGIN}/api/works/${workId}/stats`,
    (d) =>
      Object.values(d.documents_by_kind ?? {}).reduce(
        (s: number, v: any) => s + (v ?? 0),
        0,
      ) >= 1,
    { timeoutMs: 15_000, intervalMs: 2_000 },
  );
  const docCount = Object.values(stats.documents_by_kind ?? {}).reduce(
    (s: number, v: any) => s + (v ?? 0),
    0,
  );
  expect(docCount).toBeGreaterThanOrEqual(1);
});
