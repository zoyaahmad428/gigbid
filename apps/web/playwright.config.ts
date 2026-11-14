import { defineConfig } from "@playwright/test";

// Browser tests for the approval UI. The API is mocked per test, so only the Vite dev server runs.
export default defineConfig({
  testDir: "tests",
  timeout: 30_000,
  use: { baseURL: "http://localhost:5173", trace: "retain-on-failure" },
  webServer: { command: "npx vite --port 5173", url: "http://localhost:5173", reuseExistingServer: true },
});
