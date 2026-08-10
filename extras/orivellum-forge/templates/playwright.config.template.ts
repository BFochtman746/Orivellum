import { defineConfig, devices } from "@playwright/test";

// ---------------------------------------------------------------------------
// Forge Playwright Config Template
// Copy to your project root as playwright.config.ts and adjust BASE_URL.
// ---------------------------------------------------------------------------

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,   // serial within Forge; parallel browsers not needed
  forbidOnly: !!process.env.CI,
  retries: 0,             // Forge uses its own repair loop; Playwright retries are off
  workers: 1,
  reporter: [
    ["html", { outputFolder: "playwright-report", open: "never" }],
    ["json", { outputFile: "forge-jobs/PLACEHOLDER_JOB_ID/test-report.json" }],
    ["line"],
  ],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "on-first-retry",
  },

  projects: [
    // -----------------------------------------------------------------------
    // Desktop — always included for web projects
    // -----------------------------------------------------------------------
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    // {
    //   name: "firefox",
    //   use: { ...devices["Desktop Firefox"] },
    // },

    // -----------------------------------------------------------------------
    // Mobile — uncomment for mobile/PWA projects
    // -----------------------------------------------------------------------
    // {
    //   name: "webkit",
    //   use: { ...devices["Desktop Safari"] },
    // },
    // {
    //   name: "mobile-safari",
    //   use: { ...devices["iPhone 15"] },
    // },
    // {
    //   name: "mobile-chrome",
    //   use: { ...devices["Pixel 7"] },
    // },
  ],

  // -------------------------------------------------------------------------
  // Start your dev server automatically during tests.
  // Adjust command and port to match your project.
  // -------------------------------------------------------------------------
  // webServer: {
  //   command: "uv run python -m src.main",
  //   url: BASE_URL,
  //   reuseExistingServer: true,
  //   timeout: 15_000,
  // },
});
