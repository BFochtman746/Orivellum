import { defineConfig, devices } from '@playwright/test';
import { execSync } from 'node:child_process';

// ── Chromium resolution ───────────────────────────────────────────────────────
// Priority:
//   1. PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH env var (explicit override / CI)
//   2. `which chromium` / `which chromium-browser` / `which google-chrome`
//      (covers: system packages, Nix pkgs.chromium declared in replit.nix,
//       standard Linux distributions)
//   3. undefined → Playwright uses its own bundled browser (requires
//      `npx playwright install chromium` to have been run)
//
// NOTE: Do NOT use `find /nix/store` here — the Nix store is very large and
// traversing it at config-load time can crash or hang. Instead, add chromium
// to replit.nix deps (pkgs.chromium) so it appears on $PATH automatically.
function resolveChromium(): string | undefined {
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) {
    return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  }
  for (const cmd of ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable']) {
    try {
      const p = execSync(`which ${cmd} 2>/dev/null`, { encoding: 'utf8', timeout: 3000 }).trim();
      if (p) return p;
    } catch { /* not found */ }
  }
  return undefined;
}

const chromiumExecutable = resolveChromium();

// The web artifact is served by the Replit proxy at this path.
const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:80/orivellum-ui';

export default defineConfig({
  testDir: './e2e/tests',
  fullyParallel: false,   // tests share a live dev server; serialise to avoid race conditions
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'e2e/report' }]],

  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    headless: true,
  },

  globalSetup: './e2e/global-setup.ts',

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: 'e2e/.auth.json',
        launchOptions: {
          executablePath: chromiumExecutable,
          // Needed in sandboxed / Nix environments
          args: ['--no-sandbox', '--disable-setuid-sandbox'],
        },
      },
    },
  ],

  // Per-test timeout: 90 s (processing and TTS can be slow)
  timeout: 90_000,
  expect: { timeout: 20_000 },
});
