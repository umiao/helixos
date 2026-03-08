import { defineConfig } from "@playwright/test";

/**
 * Playwright E2E configuration for HelixOS frontend.
 *
 * Expects:
 *   - Backend running on http://localhost:8000
 *   - Frontend dev server on http://localhost:5173 (started via `npm run dev`)
 *
 * Run: npx playwright test
 * Or:  npm run e2e
 */
export default defineConfig({
  testDir: "./e2e",
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Limit parallel workers on CI */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use */
  reporter: process.env.CI ? "github" : "list",
  /* Shared settings for all the projects below */
  use: {
    baseURL: "http://localhost:5173",
    /* Collect trace on first retry */
    trace: "on-first-retry",
    /* Screenshot on failure */
    screenshot: "only-on-failure",
  },
  /* Test timeout: 30 seconds per test */
  timeout: 30_000,
  /* Only run in Chromium for smoke tests */
  projects: [
    {
      name: "chromium",
      use: {
        browserName: "chromium",
        headless: true,
      },
    },
  ],
});
