/**
 * Flow 6 — TTS voice/speed staleness guards
 *
 * Verifies that the Read Aloud picker never serves stale audio from a prior
 * voice or speed when the user changes settings mid-listen.
 *
 * Key invariants under test:
 *   1. A voice change triggers a new synthesis request that uses the new voice.
 *   2. A speed change triggers a new synthesis request that uses the new speed.
 *   3. Changing back to the original voice re-synthesises rather than returning
 *      a stale blob URL from the evicted cache.
 *   4. A rapid double voice change (B then C while B is in-flight) discards B's
 *      synthesis result via the session guard so the player always ends up on C.
 *
 * All tests mock POST /api/studio/tts so they never require a real TTS backend
 * and run deterministically regardless of environment.
 *
 * Bug fixed in detail.tsx (guarded by tests 3 and 4):
 *   applyTtsSettings cleared the promise map but did NOT bump ttsSessionRef, so
 *   an in-flight synthesis for the old voice could pass the stale-session check
 *   and overwrite the new voice's cache entry.  The fix bumps ttsSessionRef
 *   before capturing `session` inside applyTtsSettings.
 */

import { test, expect, type Page } from '@playwright/test';
import { goto, ensureLoggedIn, API_ORIGIN } from '../helpers';

// ── Minimal fake WAV ──────────────────────────────────────────────────────────
// 44-byte PCM WAV (header only, zero samples).  Satisfies createObjectURL and
// lets the <audio> element set its src without throwing.
const FAKE_WAV = Buffer.from(
  '52494646' + '24000000' + '57415645' +        // RIFF chunk (12 bytes)
  '666d7420' + '10000000' +                      // "fmt " + 16-byte chunk
  '0100' + '0100' + '22560000' + '22560000' +   // PCM, mono, 22050 Hz
  '0100' + '0800' +                              // block-align 1, 8-bit
  '64617461' + '00000000',                       // "data" + 0 samples
  'hex',
);

// ── Deferred promise helper ───────────────────────────────────────────────────
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => { resolve = r; });
  return { promise, release: resolve };
}

// ── Shared fixture ────────────────────────────────────────────────────────────
let targetDocTitle = '';

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  try {
    const resp = await page.request.get(`${API_ORIGIN}/api/library?limit=50`);
    if (!resp.ok()) return;
    const data  = await resp.json();
    const docs: any[] = data.documents ?? data.items ?? [];
    // Prefer docs with extracted_text so handleReadAloud can skip the chunks fetch
    const doc =
      docs.find((d: any) => d.readiness === 'ready' && d.extracted_text) ??
      docs.find((d: any) => d.readiness === 'ready');
    if (doc) {
      targetDocTitle = doc.title ?? doc.source?.split('/').pop() ?? '';
    }
  } finally {
    await page.close();
  }
});

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Navigate to the library list via the sidebar, then click the document card
 * so Wouter fires its internal navigate() (direct pushState before mount is
 * unreliable).  Waits for the "Stop and close" TTS player button to appear,
 * which confirms that the initial synthesis completed.
 */
async function openTtsPlayer(page: Page) {
  await goto(page, '/library');
  await ensureLoggedIn(page);

  const docCard = page.locator('h3').filter({ hasText: targetDocTitle }).first();
  await expect(docCard).toBeVisible({ timeout: 15_000 });
  await docCard.click();
  await page.waitForLoadState('networkidle');

  const listenBtn = page.getByRole('button', { name: /read aloud/i });
  await expect(listenBtn).toBeVisible({ timeout: 15_000 });
  await listenBtn.click();

  await expect(page.getByTitle('Stop and close')).toBeVisible({ timeout: 30_000 });
}

/**
 * The voice picker is a Radix <Select> that immediately follows the "Voice"
 * label span inside the TTS player row.  Scope via the label's parent to avoid
 * matching the lifecycle or work-assign comboboxes elsewhere on the page.
 */
function voiceTrigger(page: Page) {
  return page
    .locator('span').filter({ hasText: /^Voice$/ })
    .locator('xpath=..')
    .getByRole('combobox');
}

/**
 * Opens the voice picker and clicks the first option that is NOT already
 * selected (Radix marks the active item with aria-selected="true").
 * Returns the option text that was selected (for assertions).
 */
async function pickDifferentVoice(page: Page): Promise<string> {
  await voiceTrigger(page).click();
  const unselected = page.locator('[role="option"]:not([aria-selected="true"])').first();
  await expect(unselected).toBeVisible({ timeout: 5_000 });
  const label = (await unselected.textContent()) ?? 'unknown';
  await unselected.click();
  return label.trim();
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('TTS voice/speed staleness guards', () => {
  test.beforeEach(async ({}, testInfo) => {
    if (!targetDocTitle) {
      testInfo.skip(true, 'No ready document in library — skipping TTS staleness tests');
    }
  });

  // ── Test 1 ────────────────────────────────────────────────────────────────
  test('voice change triggers new synthesis with the new voice', async ({ page }) => {
    const requests: { voice: string; speed: number }[] = [];

    await page.route('**/studio/tts', async (route) => {
      const body = JSON.parse(route.request().postData() ?? '{}');
      requests.push({ voice: body.voice ?? '', speed: body.speed ?? 1 });
      await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: FAKE_WAV });
    });

    await openTtsPlayer(page);

    expect(requests.length).toBeGreaterThan(0);
    const initialVoice = requests[0].voice;

    await pickDifferentVoice(page);
    await page.waitForTimeout(1_500);

    // A second synthesis request must have gone out with a new voice
    expect(requests.length).toBeGreaterThanOrEqual(2);
    const lastReq = requests[requests.length - 1];
    expect(lastReq.voice).toBeTruthy();
    expect(lastReq.voice).not.toBe(initialVoice);
  });

  // ── Test 2 ────────────────────────────────────────────────────────────────
  test('speed change triggers new synthesis with the new speed', async ({ page }) => {
    const requests: { voice: string; speed: number }[] = [];

    await page.route('**/studio/tts', async (route) => {
      const body = JSON.parse(route.request().postData() ?? '{}');
      requests.push({ voice: body.voice ?? '', speed: body.speed ?? 1 });
      await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: FAKE_WAV });
    });

    await openTtsPlayer(page);
    expect(requests.length).toBeGreaterThan(0);

    // Speed buttons are plain <button> elements (not the Button component)
    // with label text like "1.5×" matching TTS_SPEED_OPTIONS[3].label
    await page.getByRole('button', { name: '1.5×' }).click();
    await page.waitForTimeout(1_500);

    expect(requests.length).toBeGreaterThanOrEqual(2);
    expect(requests[requests.length - 1].speed).toBe(1.5);
  });

  // ── Test 3 ────────────────────────────────────────────────────────────────
  // Checks that the URL cache is cleared on every voice change so that going
  // back to the original voice triggers a genuine new network request rather
  // than returning a stale blob URL.
  //
  // This would fail (requests.length would stay at 2) if applyTtsSettings did
  // not clear ttsUrlCacheRef on voice-change before re-synthesising.
  test('changing voice back to original re-synthesises (no stale cache)', async ({ page }) => {
    const requests: { voice: string }[] = [];

    await page.route('**/studio/tts', async (route) => {
      const body = JSON.parse(route.request().postData() ?? '{}');
      requests.push({ voice: body.voice ?? '' });
      await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: FAKE_WAV });
    });

    await openTtsPlayer(page);
    expect(requests.length).toBeGreaterThan(0);
    const voiceA = requests[0].voice; // e.g. "af_heart"

    // Change to voice B (any different voice)
    await pickDifferentVoice(page);
    await page.waitForTimeout(1_000);

    // Change back to voice A by picking the option with aria-selected=false that
    // matches voiceA's id.  We find the item by its accessible name which
    // Radix builds from the SelectItem's text content.
    await voiceTrigger(page).click();
    // Find the option whose text content starts with the label portion of voiceA.
    // voiceA id format: "<2-char-prefix>_<label>", e.g. "af_heart" → label "Heart"
    const labelPart = voiceA.split('_').slice(1).join('_'); // "heart" from "af_heart"
    const voiceAOption = page
      .locator('[role="option"]')
      .filter({ hasText: new RegExp(labelPart, 'i') })
      .first();
    await expect(voiceAOption).toBeVisible({ timeout: 5_000 });
    await voiceAOption.click();
    await page.waitForTimeout(1_000);

    // Three requests: initial(A) + B + A-again.
    // If the cache wasn't cleared after B, returning to A would reuse a stale
    // blob URL and skip the network call — requests.length would remain 2.
    expect(requests.length).toBeGreaterThanOrEqual(3);
    expect(requests[requests.length - 1].voice).toBe(voiceA);
  });

  // ── Test 4 ────────────────────────────────────────────────────────────────
  // Verifies the session-bump fix: a rapid B→C change while B is in-flight
  // must discard B's synthesis result via the staleness guard.
  //
  // Without the fix (missing ttsSessionRef++ in applyTtsSettings):
  //   B's synthesizePart captures session S; C's applyTtsSettings also uses S;
  //   B resolves after C and overwrites C's entry in ttsUrlCacheRef.
  //
  // With the fix:
  //   B's applyTtsSettings bumps S→S+1; C's bumps S+1→S+2; B resolves with
  //   session S+1 which no longer matches ttsSessionRef.current(S+2) → STALE.
  test('rapid double voice change: session guard discards intermediate synthesis', async ({ page }) => {
    const requests: { voice: string; n: number }[] = [];

    // Track blob URLs so we can assert which one the audio element ends up with
    const createdUrls: string[] = [];
    await page.exposeFunction('__recordBlobUrl', (url: string) => {
      createdUrls.push(url);
    });
    await page.addInitScript(() => {
      const orig = URL.createObjectURL.bind(URL);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (URL as any).createObjectURL = function (obj: unknown) {
        const url: string = orig(obj as Blob);
        (window as any).__recordBlobUrl?.(url);
        return url;
      };
    });

    // The second TTS call (voice B) will hang until released
    const bGate = deferred();
    let callCount = 0;

    await page.route('**/studio/tts', async (route) => {
      const body = JSON.parse(route.request().postData() ?? '{}');
      const n = callCount++;
      requests.push({ voice: body.voice ?? '', n });
      if (n === 1) await bGate.promise; // hold voice-B synthesis
      await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: FAKE_WAV });
    });

    await openTtsPlayer(page);
    // callCount = 1 — initial synthesis (voice A) done

    // Change to voice B — synthesis hangs.
    // We pick the first unselected option (e.g. "af_bella" when default is "af_heart").
    await pickDifferentVoice(page);
    await page.waitForTimeout(400); // B is in-flight and blocked

    // Immediately change to voice C — pick again from unselected options.
    // After selecting B, B is now aria-selected=true; pickDifferentVoice will
    // land on C (the first unselected option from the remaining list).
    await pickDifferentVoice(page);
    await page.waitForTimeout(1_200); // C synthesis completes

    // Audio src now reflects C.  There is exactly one <audio controls> element
    // in the TTS player section; use first() instead of nth(1).
    const ttsAudio = page.locator('audio[controls]').first();
    const srcAfterC = await ttsAudio.getAttribute('src');
    expect(srcAfterC).toBeTruthy();

    // Release B's blocked synthesis.  WITH the fix, B's synthesizePart
    // detects a stale session and throws — the audio src must NOT change back.
    bGate.release();
    await page.waitForTimeout(800);

    await expect(page.getByTitle('Stop and close')).toBeVisible({ timeout: 5_000 });
    const srcAfterBRelease = await ttsAudio.getAttribute('src');
    expect(srcAfterBRelease).toBe(srcAfterC);

    // Three synthesis requests: A(n=0) + B(n=1) + C(n=2)
    expect(requests.length).toBeGreaterThanOrEqual(3);
    expect(requests[0].n).toBeLessThan(requests[1].n);
    expect(requests[1].n).toBeLessThan(requests[2].n);

    // The URL active after C must be the LAST blob URL ever created
    // (B's stale result must have been discarded, not applied)
    if (createdUrls.length > 0) {
      expect(srcAfterBRelease).toBe(createdUrls[createdUrls.length - 1]);
    }
  });
});
