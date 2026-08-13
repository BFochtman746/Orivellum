/* WP2 verification: Daylight default, Hull persistence, calibration attrs,
   zero third-party font requests. Run from artifacts/orivellum-ui:
   CHROMIUM_BIN=$(which chromium) SESSION_SECRET=... node e2e/wp2-theme-verify.mjs */
import { chromium } from 'playwright-core';

const BASE = 'http://localhost:80';
const KEY = process.env.SESSION_SECRET;
if (!KEY) throw new Error('SESSION_SECRET required');

const fails = [];
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
  if (!ok) fails.push(name);
};

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_BIN || undefined,
});

// ── 1. Fresh storage → first paint is Daylight ──────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const fontHosts = [];
  ctx.on('request', (r) => {
    const u = r.url();
    if (u.includes('fonts.googleapis') || u.includes('fonts.gstatic')) fontHosts.push(u);
  });
  const page = await ctx.newPage();
  await page.addInitScript((k) => localStorage.setItem('orivellum.apiKey', k), KEY);
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });

  const theme = await page.evaluate(() => document.documentElement.dataset.theme);
  check('fresh storage boots Daylight', theme === 'daylight', `data-theme=${theme}`);

  const hasDark = await page.evaluate(() => document.documentElement.classList.contains('dark'));
  check('no .dark class in Daylight', !hasDark);

  const scheme = await page.evaluate(() => document.documentElement.style.colorScheme);
  check('colorScheme light', scheme === 'light', scheme);

  const bg = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--gd-bg').trim());
  check('canvas token resolves to Daylight bone', bg.toUpperCase() === '#F4F1E9', bg);

  const meta = await page.evaluate(() =>
    document.querySelector('meta[name="theme-color"]')?.getAttribute('content'));
  check('theme-color meta is Daylight', (meta || '').toUpperCase() === '#F4F1E9', meta);

  check('zero third-party font requests', fontHosts.length === 0, fontHosts.join(', '));
  await page.screenshot({ path: '/tmp/wp2-daylight.png' });

  // ── 2. Switch to Hull via theme API, verify + persist ─────────────────────
  await page.evaluate(() => {
    localStorage.setItem('orivellum-theme', 'hull');
    localStorage.setItem('orivellum-text-size', '112');
    localStorage.setItem('orivellum-measure', 'focused');
    localStorage.setItem('orivellum-reading-face', 'serif');
  });
  await page.reload({ waitUntil: 'networkidle' });

  const d = await page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    dark: document.documentElement.classList.contains('dark'),
    textSize: document.documentElement.dataset.textSize,
    measure: document.documentElement.dataset.measure,
    face: document.documentElement.dataset.readingFace,
    bg: getComputedStyle(document.documentElement).getPropertyValue('--gd-bg').trim(),
    meta: document.querySelector('meta[name="theme-color"]')?.getAttribute('content'),
    rootPx: parseFloat(getComputedStyle(document.documentElement).fontSize),
    measureVar: getComputedStyle(document.documentElement).getPropertyValue('--editor-measure').trim(),
  }));
  check('hull persists across reload', d.theme === 'hull', JSON.stringify(d));
  check('.dark class applied in Hull', d.dark);
  check('hull canvas token', d.bg.toUpperCase() === '#14181D', d.bg);
  check('hull theme-color meta', (d.meta || '').toUpperCase() === '#14181D', d.meta);
  check('text-size 112 scales root', Math.abs(d.rootPx - 16 * 1.12) < 0.3, `${d.rootPx}px`);
  check('measure focused → 62ch', d.measureVar === '62ch', d.measureVar);
  check('reading face serif attr', d.face === 'serif');
  await page.screenshot({ path: '/tmp/wp2-hull.png' });
  await ctx.close();
}

// ── 3. Cross-install restore: fresh device adopts server-saved prefs ────────
{
  // Seed the server record directly (merge PUT), then boot a fresh context.
  const seed = await fetch('http://localhost:8080/api/system/settings/ui-preferences', {
    method: 'PUT',
    headers: { 'X-Api-Key': KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ theme: 'hull', readingFace: 'serif' }),
  });
  check('seed server record', seed.ok, String(seed.status));

  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.addInitScript((k) => localStorage.setItem('orivellum.apiKey', k), KEY);
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === 'hull',
    null, { timeout: 5000 },
  ).catch(() => {});
  const d = await page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    face: document.documentElement.dataset.readingFace,
    stored: localStorage.getItem('orivellum-theme'),
  }));
  check('fresh device hydrates hull from server', d.theme === 'hull', JSON.stringify(d));
  check('hydrated prefs persist locally', d.stored === 'hull');
  check('hydration restores calibration too', d.face === 'serif');
  await ctx.close();

  // Restore server record to defaults so reruns stay deterministic.
  await fetch('http://localhost:8080/api/system/settings/ui-preferences', {
    method: 'PUT',
    headers: { 'X-Api-Key': KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ theme: 'daylight', textSize: '100', measure: 'standard', readingFace: 'sans' }),
  });
}

// ── 4. System preference mode follows OS scheme ─────────────────────────────
{
  const ctx = await browser.newContext({ colorScheme: 'dark' });
  const page = await ctx.newPage();
  await page.addInitScript((k) => {
    localStorage.setItem('orivellum.apiKey', k);
    localStorage.setItem('orivellum-theme', 'system');
  }, KEY);
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  const theme = await page.evaluate(() => document.documentElement.dataset.theme);
  check('system pref + dark OS → hull', theme === 'hull', theme);
  await ctx.close();
}

await browser.close();
if (fails.length) {
  console.error(`\n${fails.length} FAILURES: ${fails.join('; ')}`);
  process.exit(1);
}
console.log('\nALL WP2 CHECKS PASSED');
