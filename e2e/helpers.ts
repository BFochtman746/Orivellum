/**
 * Shared helpers for the Orivellum E2E suite.
 */
import type { Page } from '@playwright/test';
import { expect } from '@playwright/test';

export const BASE_PATH = '/orivellum-ui';
// Full origin of the Replit proxy (the built SPA is served via the API server at port 8080,
// routed through the proxy at port 80).
export const WEB_ORIGIN = process.env.E2E_WEB_ORIGIN ?? 'http://localhost:80';
export const API_ORIGIN = process.env.E2E_API_ORIGIN ?? 'http://localhost:8080';

// ── Navigation ────────────────────────────────────────────────────────────────

/**
 * Maps SPA routes to their sidebar section header text and link label.
 * The built bundle's Wouter router doesn't handle direct deep-link navigation
 * correctly (base-path stripping on first mount with a stale build), so we
 * always start from root and click the sidebar Link which uses Wouter's
 * internal navigate() call — that path works reliably.
 */
const NAV_MAP: Record<string, { section: string; label: string }> = {
  '/library': { section: 'IMPORT', label: 'Library' },
  '/studio':  { section: 'CREATE', label: 'Studio' },
  '/write':   { section: 'CREATE', label: 'Write desk' },
  '/works':   { section: 'UNDERSTAND', label: 'Works' },
  '/chat':    { section: 'UNDERSTAND', label: 'Chat' },
  '/books':   { section: 'UNDERSTAND', label: 'Books' },
  '/learn':   { section: 'UNDERSTAND', label: 'Learn' },
  '/topics':  { section: 'UNDERSTAND', label: 'Topics' },
  '/actions': { section: 'ACT', label: 'Actions' },
  '/review':  { section: 'REVIEW', label: 'Review Queue' },
  '/governance': { section: 'REVIEW', label: 'Governance' },
  '/system':  { section: 'SETTINGS', label: 'System' },
  '/backups': { section: 'SETTINGS', label: 'Backups' },
  '/mcos':    { section: 'SETTINGS', label: 'Calibration' },
};

/**
 * Navigate to a web-app route and wait for the React app to render.
 *
 * Strategy:
 * 1. If already at root and the route is known, expand the sidebar section
 *    and click the link — this triggers Wouter's in-app navigation.
 * 2. For sub-routes (e.g. /library/:docId), navigate to the parent first,
 *    then use JS pushState (in-app state is already initialised by that point).
 * 3. For the root ("/"), just navigate directly.
 */
export async function goto(page: Page, route: string) {
  const rootUrl = `${WEB_ORIGIN}${BASE_PATH}/`;

  // Root: direct navigation always works
  if (route === '/') {
    await page.goto(rootUrl);
    await page.waitForLoadState('networkidle');
    return;
  }

  // Check if there's a known sidebar entry for this route
  const navEntry = NAV_MAP[route];
  if (navEntry) {
    // Ensure we're at root with the app loaded
    const currentUrl = page.url();
    if (!currentUrl.startsWith(`${WEB_ORIGIN}${BASE_PATH}`)) {
      await page.goto(rootUrl);
      await page.waitForLoadState('networkidle');
    } else if (!currentUrl.includes('/library') && !currentUrl.includes('/works') &&
               !currentUrl.includes('/studio') && !currentUrl.includes('/chat') &&
               !currentUrl.endsWith('/')) {
      // Not sure of state — reload root to be safe
      await page.goto(rootUrl);
      await page.waitForLoadState('networkidle');
    }

    // Expand the sidebar section and click the link
    await expandSectionAndNavigate(page, navEntry.section, navEntry.label);
    return;
  }

  // Sub-route: navigate to parent via sidebar, then use pushState to hit the
  // specific sub-route once the app is initialised
  const parentRoute = '/' + route.split('/').filter(Boolean)[0];
  const parentEntry = NAV_MAP[parentRoute];
  if (parentEntry) {
    // Ensure root loaded
    const currentUrl = page.url();
    const alreadyAtParent = currentUrl.includes(`${BASE_PATH}${parentRoute}`);
    if (!alreadyAtParent) {
      const u = page.url();
      if (!u.startsWith(`${WEB_ORIGIN}${BASE_PATH}`)) {
        await page.goto(rootUrl);
        await page.waitForLoadState('networkidle');
      }
      await expandSectionAndNavigate(page, parentEntry.section, parentEntry.label);
    }
    // Now use Wouter's navigate via JS — app is already mounted so it works
    await page.evaluate(
      ([base, r]) => {
        window.history.pushState({}, '', base + r);
        window.dispatchEvent(new Event('pushState'));
        window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
      },
      [BASE_PATH, route] as [string, string],
    );
    await page.waitForLoadState('networkidle');
    return;
  }

  // Fallback: direct navigation
  await page.goto(`${WEB_ORIGIN}${BASE_PATH}${route}`);
  await page.waitForLoadState('networkidle');
}

/**
 * Click the sidebar accordion section header to expand it, then click the
 * named navigation link inside it.  Uses force-click to handle any
 * transition/animation overlap.
 */
async function expandSectionAndNavigate(
  page: Page,
  sectionName: string,
  linkLabel: string,
) {
  // The section header appears as a button containing the section text
  const sectionBtn = page
    .getByRole('button', { name: new RegExp(`^${sectionName}$`, 'i') })
    .or(page.locator(`button:has-text("${sectionName}")`).first());

  // Click to expand (may already be open; clicking again closes it, so only
  // click when the link isn't yet visible)
  const link = page.getByRole('link', { name: new RegExp(`^${linkLabel}$`, 'i') });
  const linkVisible = await link.isVisible({ timeout: 500 }).catch(() => false);
  if (!linkVisible) {
    await sectionBtn.click({ timeout: 10_000 });
    await page.waitForTimeout(300); // allow accordion animation
  }

  // Now click the nav link
  await expect(link).toBeVisible({ timeout: 5_000 });
  await link.click();
  await page.waitForLoadState('networkidle');
}

// ── Auth guard ────────────────────────────────────────────────────────────────

/**
 * If the login form is visible, fill it with the session secret and submit.
 * The app shows "Connecting…" while auth is being checked, then either the
 * login form or the app itself.
 */
export async function ensureLoggedIn(page: Page) {
  // Wait for the "Connecting…" placeholder to disappear
  const connecting = page.getByText('Connecting…');
  if (await connecting.isVisible({ timeout: 2_000 }).catch(() => false)) {
    await expect(connecting).not.toBeVisible({ timeout: 10_000 });
  }

  const apiKey = process.env.SESSION_SECRET ?? process.env.E2E_API_KEY ?? '';
  const loginInput = page.locator('input[type="password"][placeholder="API key"]');
  if (await loginInput.isVisible({ timeout: 2_000 }).catch(() => false)) {
    if (!apiKey) throw new Error('Login screen shown but no API key is set');
    await loginInput.fill(apiKey);
    await page.getByRole('button', { name: /continue/i }).click();
    await expect(loginInput).not.toBeVisible({ timeout: 10_000 });
  }
}

// ── Polling helpers ───────────────────────────────────────────────────────────

/**
 * Poll an API endpoint until the predicate returns true or timeout is reached.
 * Returns the final parsed JSON.
 */
export async function pollUntil<T>(
  page: Page,
  url: string,
  predicate: (data: T) => boolean,
  opts: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<T> {
  const { intervalMs = 2_000, timeoutMs = 60_000 } = opts;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const resp = await page.request.get(url);
    if (resp.ok()) {
      const data: T = await resp.json();
      if (predicate(data)) return data;
    }
    await page.waitForTimeout(intervalMs);
  }
  throw new Error(`pollUntil timed out after ${timeoutMs}ms — URL: ${url}`);
}
