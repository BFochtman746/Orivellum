/**
 * WP0 baseline screenshot capture.
 *
 * Captures the five primary screens (Home, Chat, Works, Library, Work detail)
 * at three widths (320px, iPhone portrait 390x844, desktop 1440px) into
 * baseline/screenshots/ at the repo root.
 *
 * Run from the repo root with both dev workflows running:
 *   CHROMIUM_BIN=$(which chromium) node artifacts/orivellum-ui/scripts/capture-baseline.mjs
 *
 * Auth: seeds localStorage with the login key (ORIVELLUM_LOGIN_KEY or
 * SESSION_SECRET) before page load — the client's stored-key fallback then
 * establishes the session itself. The key is never written to disk.
 */
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const KEY = process.env.ORIVELLUM_LOGIN_KEY || process.env.SESSION_SECRET || '';
// The dev proxy strips the /orivellum-ui base — the SPA serves at ROOT here.
const BASE = (process.env.BASELINE_BASE_URL || 'http://localhost:80').replace(/\/$/, '');
const API = (process.env.BASELINE_API_URL || 'http://localhost:80/api').replace(/\/$/, '');
const OUT = process.env.BASELINE_OUT || path.join(process.cwd(), 'baseline', 'screenshots');

if (!KEY) {
  console.error('No login key: set ORIVELLUM_LOGIN_KEY or SESSION_SECRET.');
  process.exit(2);
}
if (!process.env.ORIVELLUM_LOGIN_KEY && process.env.SESSION_SECRET) {
  console.warn(
    'WARNING: using SESSION_SECRET as the login key (deprecated fallback). ' +
      'Set a dedicated ORIVELLUM_LOGIN_KEY instead.',
  );
}

const EXPECTED_CAPTURES = 15; // 5 screens x 3 viewports — fail closed on fewer

const VIEWPORTS = [
  { name: 'w320', width: 320, height: 568 },
  { name: 'iphone', width: 390, height: 844 },
  { name: 'w1440', width: 1440, height: 900 },
];

async function firstWorkId() {
  const res = await fetch(`${API}/works`, { headers: { Authorization: `Bearer ${KEY}` } });
  if (!res.ok) throw new Error(`GET /works failed: ${res.status}`);
  const data = await res.json();
  const works = data.works || data;
  return Array.isArray(works) && works.length ? works[0].id : null;
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  const workId = await firstWorkId();
  const screens = [
    { name: 'home', path: '/' },
    { name: 'chat', path: '/chat' },
    { name: 'works', path: '/works' },
    { name: 'library', path: '/library' },
  ];
  if (!workId) {
    console.error('No Works exist — cannot capture the work-detail screen. Seed a Work first.');
    process.exit(1);
  }
  screens.push({ name: 'work-detail', path: `/works/${workId}` });

  let captured = 0;
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_BIN || undefined,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  try {
    for (const vp of VIEWPORTS) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        deviceScaleFactor: 1,
      });
      await context.addInitScript((key) => {
        try {
          localStorage.setItem('orivellum.apiKey', key);
        } catch {}
      }, KEY);
      const page = await context.newPage();
      for (const screen of screens) {
        const url = `${BASE}${screen.path === '/' ? '/' : screen.path}`;
        await page.goto(url, { waitUntil: 'load', timeout: 45000 });
        try {
          await page.waitForLoadState('networkidle', { timeout: 12000 });
        } catch {
          // polling endpoints can keep the network busy — proceed after settle delay
        }
        await page.waitForTimeout(1500);
        // Fail closed on unauthenticated/unrouted renders — a baseline of
        // login forms or 404 pages is worse than no baseline.
        const bodyText = await page.evaluate(() => document.body.innerText || '');
        if (/Page not found/i.test(bodyText)) {
          throw new Error(`${screen.name} at ${vp.name} rendered the 404 page (${url})`);
        }
        if (/API key/i.test(bodyText) && /log ?in|sign ?in|unlock/i.test(bodyText)) {
          throw new Error(`${screen.name} at ${vp.name} rendered the login form — auth failed (${url})`);
        }
        const file = path.join(OUT, `${screen.name}-${vp.name}.png`);
        await page.screenshot({ path: file });
        captured += 1;
        console.log(`captured ${path.relative(process.cwd(), file)}`);
      }
      await context.close();
    }
  } finally {
    await browser.close();
  }
  if (captured !== EXPECTED_CAPTURES) {
    throw new Error(`expected ${EXPECTED_CAPTURES} captures, produced ${captured}`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
