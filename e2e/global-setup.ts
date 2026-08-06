/**
 * Playwright global setup — authenticates once and saves the session cookie.
 *
 * The Chromium executable is resolved the same way as in playwright.config.ts:
 * env var → PATH (`which chromium`) → Playwright bundled.
 * Chromium must be available on PATH (declare pkgs.chromium in replit.nix) or
 * set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH before running `pnpm test:e2e`.
 *
 * Authentication priority:
 *   1. E2E_API_KEY  env var   (explicit CI secret)
 *   2. SESSION_SECRET env var (Replit workspace secret — the server accepts it
 *      as the API key, so it doubles as the test credential without revealing
 *      its value in tracked files)
 *   3. data/api_key.txt       (local dev file written by the server on first start)
 */
import { chromium, type FullConfig } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';

const API_ORIGIN = process.env.E2E_API_ORIGIN ?? 'http://localhost:8080';
const AUTH_FILE  = path.resolve('e2e/.auth.json');

function resolveChromium(): string | undefined {
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) {
    return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  }
  for (const cmd of ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable']) {
    try {
      const p = execSync(`which ${cmd} 2>/dev/null`, { encoding: 'utf8', timeout: 3000 }).trim();
      if (p) return p;
    } catch { /* not in PATH */ }
  }
  return undefined;
}

function readApiKey(): string {
  if (process.env.E2E_API_KEY?.trim()) return process.env.E2E_API_KEY.trim();
  if (process.env.SESSION_SECRET?.trim()) return process.env.SESSION_SECRET.trim();

  const keyFile = path.resolve('data/api_key.txt');
  if (fs.existsSync(keyFile)) {
    const raw = fs.readFileSync(keyFile, 'utf8').trim();
    if (raw) return raw;
  }

  throw new Error(
    'Cannot find an API key for the E2E suite.\n' +
    'Set the E2E_API_KEY or SESSION_SECRET environment variable, ' +
    'or ensure data/api_key.txt exists (written automatically by the server on first start).',
  );
}

export default async function globalSetup(_config: FullConfig) {
  const apiKey = readApiKey();

  const browser = await chromium.launch({
    executablePath: resolveChromium(),
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const context = await browser.newContext();
  const page    = await context.newPage();

  const resp = await page.request.post(`${API_ORIGIN}/api/auth/login`, {
    data: { key: apiKey },
    headers: { 'Content-Type': 'application/json' },
  });

  if (!resp.ok()) {
    await browser.close();
    throw new Error(
      `Login failed (${resp.status()}) — check that the API key matches the running server's key.`,
    );
  }

  fs.mkdirSync(path.dirname(AUTH_FILE), { recursive: true });
  await context.storageState({ path: AUTH_FILE });
  await browser.close();
  console.log('[global-setup] authenticated and saved session to', AUTH_FILE);
}
