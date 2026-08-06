/**
 * Flow 4 — TTS synthesis via the Studio UI → "Audiobook ready" appears on screen
 *
 * 1. Check TTS availability; skip if unavailable.
 * 2. Navigate to Studio → Build Audiobook tab (within Voice Studio).
 * 3. Select "Single Document" mode and pick the sample fixture document.
 * 4. Click "Generate Audiobook".
 * 5. Assert the "Audiobook ready" card appears on screen (hard assertion).
 */
import { test, expect } from '@playwright/test';
import { goto, ensureLoggedIn, API_ORIGIN } from '../helpers';

test('TTS — generate audiobook via Studio UI → "Audiobook ready" card appears', async ({ page }) => {
  // ── Preflight: check TTS availability ────────────────────────────────────
  const statusResp = await page.request.get(`${API_ORIGIN}/api/studio/status`);
  if (!statusResp.ok()) {
    test.skip(true, 'Studio status endpoint unreachable — skipping TTS test');
    return;
  }
  const status = await statusResp.json();
  if (!status?.tts?.available) {
    test.skip(true, `TTS backend unavailable (available=${status?.tts?.available}) — skipping`);
    return;
  }

  // ── Find a ready document to synthesise ──────────────────────────────────
  const libResp = await page.request.get(`${API_ORIGIN}/api/library?limit=30`);
  expect(libResp.ok(), 'library endpoint must be reachable').toBeTruthy();
  const libData = await libResp.json();
  const allDocs: any[] = libData.documents ?? libData.items ?? [];
  const readyDocs = allDocs.filter((d: any) => d.readiness === 'ready');
  if (readyDocs.length === 0) {
    test.skip(true, 'No ready documents in library — skipping TTS test');
    return;
  }
  // Prefer the fixture document if present; otherwise use the first ready doc
  const targetDoc =
    readyDocs.find((d: any) => (d.title ?? '').toLowerCase().includes('sample')) ??
    readyDocs[0];
  const targetTitle: string =
    targetDoc.title ?? targetDoc.source?.split('/').pop() ?? 'document';

  // ── Navigate to Studio ────────────────────────────────────────────────────
  await goto(page, '/studio');
  await ensureLoggedIn(page);

  // Confirm Voice Studio tab is visible (it's the default)
  await expect(page.getByRole('button', { name: /voice studio/i })).toBeVisible({
    timeout: 10_000,
  });

  // ── Click "Build Audiobook" sub-tab inside Voice Studio ──────────────────
  // VoiceStudio renders four sub-tabs as <button> elements.
  const buildAudiobookTab = page
    .getByRole('button', { name: /build audiobook/i })
    .first();
  await expect(buildAudiobookTab).toBeVisible({ timeout: 10_000 });
  await buildAudiobookTab.click();

  // ── Select "Single Document" source mode ─────────────────────────────────
  const singleDocBtn = page.getByRole('button', { name: /single document/i });
  await expect(singleDocBtn).toBeVisible({ timeout: 5_000 });
  await singleDocBtn.click();

  // ── Pick a document from the Select dropdown ──────────────────────────────
  // The document Select trigger initially shows "Select a document…".
  // We click it once, then pick the option; the trigger text then changes to
  // the document title. We must not re-use the filter-by-placeholder locator
  // after selection because the filter no longer matches.
  const docSelectTrigger = page
    .getByRole('combobox')
    .filter({ hasText: /select a document/i })
    .first();
  await expect(docSelectTrigger).toBeVisible({ timeout: 5_000 });
  await docSelectTrigger.click();

  // Radix SelectContent opens as a portal with role="listbox" / role="option"
  const docOption = page
    .getByRole('option', { name: new RegExp(targetTitle.slice(0, 20), 'i') })
    .first();
  await expect(docOption).toBeVisible({ timeout: 5_000 });
  await docOption.click();

  // After selection the Generate Audiobook button must become enabled
  // (it is disabled while no document is selected).
  const generateBtn = page.getByRole('button', { name: /generate audiobook/i });
  await expect(generateBtn).toBeEnabled({ timeout: 5_000 });

  // ── Click "Generate Audiobook" ────────────────────────────────────────────
  await generateBtn.click();

  // ── Assert "Audiobook ready" card appears on screen ──────────────────────
  // The card is rendered once the blob is received and audioUrl state is set.
  // espeak on a ~150-word doc completes in well under 60 s.
  await expect(page.getByText('Audiobook ready')).toBeVisible({ timeout: 120_000 });
});
