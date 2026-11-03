import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the running stack.
 *
 * Expects the backend at E2E_API_URL (default http://localhost:8000) seeded with
 * the sample dataset and started with PLACES_PROVIDER=mock, and the frontend at
 * E2E_BASE_URL (default http://localhost:3000). See docs/setup.md#testing.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:3000",
    trace: "retain-on-failure",
    // Use the pre-installed Chromium when present (CI images may set this).
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH } : {},
  },
  projects: [
    { name: "mobile", use: { ...devices["Pixel 7"] } },
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
  ],
});
