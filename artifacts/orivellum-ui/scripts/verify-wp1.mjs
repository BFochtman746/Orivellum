/**
 * WP1 shell verification.
 *
 * For a set of routes at phone (320/390), tablet (768) and desktop (1440)
 * widths, asserts:
 *   - the page renders inside the ResponsiveShell (tab bar OR rail present)
 *   - no page-level horizontal scrolling at 320px
 *   - no 404/login renders
 * and saves screenshots for visual review.
 *
 * Run from repo root with the dev workflows up:
 *   CHROMIUM_BIN=$(which chromium) node artifacts/orivellum-ui/scripts/verify-wp1.mjs
 */
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const KEY = process.env.ORIVELLUM_LOGIN_KEY || process.env.SESSION_SECRET || '';
const BASE = (process.env.BASELINE_BASE_URL || 'http://localhost:80').replace(/\/$/, '');
const OUT = process.env.WP1_OUT || '/tmp/wp1-shots';

if (!KEY) {
  console.error('No login key: set ORIVELLUM_LOGIN_KEY or SESSION_SECRET.');
  process.exit(2);
}

const VIEWPORTS = [
  { name: 'w320', width: 320, height: 568 },
  { name: 'iphone', width: 390, height: 844 },
  { name: 'w768', width: 768, height: 900 },
  { name: 'w1440', width: 1440, height: 900 },
];

const SCREENS = [
  { name: 'home', path: '/' },
  { name: 'chat', path: '/chat' },
  { name: 'writing', path: '/writing' },
  { name: 'library', path: '/library' },
  { name: 'system', path: '/system' },
  { name: 'learning', path: '/learning' },
];

const failures = [];

async function main() {
  mkdirSync(OUT, { recursive: true });
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
        try { localStorage.setItem('orivellum.apiKey', key); } catch {}
      }, KEY);
      const page = await context.newPage();
      for (const screen of SCREENS) {
        const url = `${BASE}${screen.path === '/' ? '/' : screen.path}`;
        await page.goto(url, { waitUntil: 'load', timeout: 45000 });
        try { await page.waitForLoadState('networkidle', { timeout: 8000 }); } catch {}
        await page.waitForTimeout(1200);

        const info = await page.evaluate(() => {
          const de = document.documentElement;
          return {
            body: (document.body.innerText || '').slice(0, 4000),
            hasTabbar: !!document.querySelector('.shell-tabbar') &&
              getComputedStyle(document.querySelector('.shell-tabbar')).display !== 'none',
            hasRail: !!document.querySelector('.shell-rail') &&
              getComputedStyle(document.querySelector('.shell-rail')).display !== 'none',
            hScroll: de.scrollWidth > de.clientWidth + 1,
            scrollWidth: de.scrollWidth,
            clientWidth: de.clientWidth,
          };
        });

        const tag = `${screen.name}@${vp.name}`;
        if (/Page not found/i.test(info.body)) failures.push(`${tag}: 404 page`);
        if (/API key/i.test(info.body) && /log ?in|sign ?in|unlock/i.test(info.body))
          failures.push(`${tag}: login form (auth failed)`);
        const mobile = vp.width < 768;
        if (mobile && !info.hasTabbar) failures.push(`${tag}: mobile tab bar missing`);
        if (!mobile && !info.hasRail) failures.push(`${tag}: rail missing`);
        if (mobile && info.hasRail) failures.push(`${tag}: rail visible on mobile`);
        if (!mobile && info.hasTabbar) failures.push(`${tag}: tab bar visible on desktop`);
        if (vp.width === 320 && info.hScroll)
          failures.push(`${tag}: horizontal page scroll (${info.scrollWidth}>${info.clientWidth})`);

        await page.screenshot({ path: path.join(OUT, `${screen.name}-${vp.name}.png`) });
        console.log(`ok ${tag}`);
      }

      // More sheet — open it on the home screen at this viewport
      const moreBtn = vp.width < 768 ? '[data-testid="tab-more"]' : '[data-testid="rail-more"]';
      await page.goto(`${BASE}/`, { waitUntil: 'load', timeout: 45000 });
      await page.waitForTimeout(800);
      await page.click(moreBtn);
      await page.waitForTimeout(600);
      const sheetOk = await page.evaluate(() => !!document.querySelector('.shell-sheet-row'));
      if (!sheetOk) failures.push(`more-sheet@${vp.name}: sheet rows missing`);
      await page.screenshot({ path: path.join(OUT, `more-sheet-${vp.name}.png`) });
      console.log(`ok more-sheet@${vp.name}`);

      await context.close();
    }

    // ── Deep-link / back-button / redirect behavior (phone viewport) ──────
    {
      const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
      await context.addInitScript((key) => {
        try { localStorage.setItem('orivellum.apiKey', key); } catch {}
      }, KEY);
      const page = await context.newPage();
      const worksRes = await fetch(`${BASE}/api/works`, { headers: { Authorization: `Bearer ${KEY}` } });
      const workId = ((await worksRes.json()).works ?? [])[0]?.id;

      const deepLinks = workId
        ? [
            { path: `/works/${workId}`, tab: 'works' },
            { path: `/works/${workId}/intelligence`, tab: 'works' },
            { path: '/learning/review', tab: 'more' },
            { path: '/mail', tab: 'more' },
          ]
        : [];
      for (const dl of deepLinks) {
        await page.goto(`${BASE}${dl.path}`, { waitUntil: 'load', timeout: 45000 });
        await page.waitForTimeout(1200);
        const body = await page.evaluate(() => document.body.innerText || '');
        if (/Page not found/i.test(body)) failures.push(`deep-link ${dl.path}: 404`);
        if (!(await page.$('.shell-tabbar'))) failures.push(`deep-link ${dl.path}: not inside shell`);
        const act = await page.getAttribute(`[data-testid="tab-${dl.tab}"]`, 'data-active');
        if (act !== 'true') failures.push(`deep-link ${dl.path}: ${dl.tab} tab not active`);
        console.log(`ok deep-link ${dl.path}`);
      }

      if (workId) {
        // back button through the shell tabs
        await page.goto(`${BASE}/works/${workId}`, { waitUntil: 'load', timeout: 45000 });
        await page.waitForTimeout(800);
        await page.click('[data-testid="tab-library"]');
        await page.waitForTimeout(800);
        if (!page.url().includes('/library')) failures.push('tab click did not navigate to /library');
        await page.goBack();
        await page.waitForTimeout(800);
        if (!page.url().includes(`/works/${workId}`)) failures.push(`back button broke: ${page.url()}`);
        console.log('ok back-button');
      }

      // legacy /files redirect must still land on /library
      await page.goto(`${BASE}/files`, { waitUntil: 'load', timeout: 45000 });
      await page.waitForTimeout(800);
      if (!page.url().includes('/library')) failures.push('/files redirect broken');
      console.log('ok /files redirect');

      await context.close();
    }
  } finally {
    await browser.close();
  }
  if (failures.length) {
    console.error('\nFAILURES:\n' + failures.map((f) => ` - ${f}`).join('\n'));
    process.exit(1);
  }
  console.log('\nWP1 shell verification passed.');
}

main().catch((err) => { console.error(err); process.exit(1); });
