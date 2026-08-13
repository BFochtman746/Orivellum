#!/usr/bin/env node
/**
 * Core Web Vitals measurement (WP5) — hermetic lab check of the built app.
 *
 * Serves dist/ via `vite preview`, stubs every /api call (no backend needed),
 * loads the Home screen in Chromium and measures:
 *   - LCP  (largest-contentful-paint)     budget ≤ 2500 ms
 *   - CLS  (layout-shift, buffered)       budget ≤ 0.1
 *   - INP  (event timing on a real click) budget ≤ 200 ms, best-effort
 *
 * Lab numbers on CI hardware are noisy; budgets are enforced for LCP/CLS and
 * reported for INP (enforced only when an interaction was actually measured).
 *
 * Usage: node scripts/measure_web_vitals.mjs   (after `pnpm run build`)
 * Env:   PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH — optional chromium binary.
 */
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 4173;
const BASE_PATH = process.env.BASE_PATH ?? '/orivellum-ui/';
const URL_HOME = `http://127.0.0.1:${PORT}${BASE_PATH}`;

const BUDGET_LCP_MS = 2500;
const BUDGET_CLS = 0.1;
const BUDGET_INP_MS = 200;

// ── Start vite preview ───────────────────────────────────────────────────────
const preview = spawn('pnpm', ['exec', 'vite', 'preview', '--config', 'vite.config.ts', '--host', '127.0.0.1'], {
  cwd: ROOT,
  env: { ...process.env, PORT: String(PORT), BASE_PATH },
  stdio: ['ignore', 'pipe', 'pipe'],
});
const stop = () => { try { preview.kill('SIGTERM'); } catch { /* already gone */ } };
process.on('exit', stop);

await new Promise((resolve, reject) => {
  const t = setTimeout(() => reject(new Error('vite preview did not start within 30 s')), 30_000);
  preview.stdout.on('data', (d) => { if (String(d).includes('Local:')) { clearTimeout(t); resolve(); } });
  preview.on('exit', (code) => reject(new Error(`vite preview exited early (${code})`)));
});

// ── Measure ──────────────────────────────────────────────────────────────────
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
  args: ['--no-sandbox'],
});
let failures = [];
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  // Hermetic: stub the API so the AUTHENTICATED Home screen renders without a
  // backend. /api/auth/me must report authenticated:true — otherwise the app
  // shows the login form and the vitals numbers would measure the wrong page.
  await context.route('**/api/**', (route) => {
    const url = route.request().url();
    let body = '{}';
    if (url.includes('/api/auth/me')) body = '{"authenticated":true}';
    return route.fulfill({ status: 200, contentType: 'application/json', body });
  });
  const page = await context.newPage();
  await page.addInitScript(() => {
    window.__vitals = { lcp: 0, cls: 0, inp: 0 };
    new PerformanceObserver((l) => {
      for (const e of l.getEntries()) window.__vitals.lcp = e.startTime;
    }).observe({ type: 'largest-contentful-paint', buffered: true });
    new PerformanceObserver((l) => {
      for (const e of l.getEntries()) if (!e.hadRecentInput) window.__vitals.cls += e.value;
    }).observe({ type: 'layout-shift', buffered: true });
    new PerformanceObserver((l) => {
      for (const e of l.getEntries()) {
        if (e.interactionId) window.__vitals.inp = Math.max(window.__vitals.inp, e.duration);
      }
    }).observe({ type: 'event', buffered: true, durationThreshold: 16 });
  });

  await page.goto(URL_HOME, { waitUntil: 'networkidle', timeout: 60_000 });
  // Guard: we must be measuring the authenticated shell, not the login form.
  // The login form is the only surface with a password input.
  const onLogin = await page.locator('input[type="password"]').count();
  if (onLogin > 0) {
    failures.push('measured page is the LOGIN form, not the authenticated Home — auth stub broken');
  }
  await page.waitForTimeout(3000); // let LCP settle
  // A real interaction for INP (tap the body — safe on any screen).
  await page.mouse.click(195, 400);
  await page.waitForTimeout(1500);

  const vitals = await page.evaluate(() => window.__vitals);
  console.log(`LCP: ${vitals.lcp.toFixed(0)} ms  (budget ${BUDGET_LCP_MS} ms)`);
  console.log(`CLS: ${vitals.cls.toFixed(4)}  (budget ${BUDGET_CLS})`);
  console.log(`INP: ${vitals.inp ? `${vitals.inp.toFixed(0)} ms` : 'not measured'}  (budget ${BUDGET_INP_MS} ms, best-effort)`);

  if (vitals.lcp === 0) failures.push('LCP was never observed — page may not have rendered');
  if (vitals.lcp > BUDGET_LCP_MS) failures.push(`LCP ${vitals.lcp.toFixed(0)} ms exceeds ${BUDGET_LCP_MS} ms`);
  if (vitals.cls > BUDGET_CLS) failures.push(`CLS ${vitals.cls.toFixed(4)} exceeds ${BUDGET_CLS}`);
  if (vitals.inp && vitals.inp > BUDGET_INP_MS) failures.push(`INP ${vitals.inp.toFixed(0)} ms exceeds ${BUDGET_INP_MS} ms`);
} finally {
  await browser.close();
  stop();
}

if (failures.length) {
  console.error('\n✗ WEB VITALS FAILURES:');
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log('\n✓ Core Web Vitals within budget');
