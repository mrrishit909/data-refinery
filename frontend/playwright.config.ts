import { defineConfig } from "@playwright/test";

// E2E_BASE_URL set: test the live Pages site. Unset: test the local production preview.
const live = process.env.E2E_BASE_URL;
export const EVIDENCE = process.env.E2E_EVIDENCE_DIR ?? "../evidence/e2e";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  retries: live ? 1 : 0,
  workers: 2,
  reporter: [["list"], ["./tests/e2e/summary-reporter.ts"]],
  use: {
    baseURL: live ?? "http://127.0.0.1:4174/data-refinery/",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "mobile-390", use: { viewport: { width: 390, height: 844 }, hasTouch: true, deviceScaleFactor: 2 } },
    { name: "tablet-768", use: { viewport: { width: 768, height: 1024 }, hasTouch: true } },
    { name: "desktop-1440", use: { viewport: { width: 1440, height: 900 } } },
  ],
  webServer: live
    ? undefined
    : {
        command: "npx vite preview --port 4174 --strictPort --host 127.0.0.1",
        url: "http://127.0.0.1:4174/data-refinery/",
        reuseExistingServer: true,
        timeout: 60_000,
      },
});
