import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const baseUrl = process.env.CONSOLE_E2E_BASE_URL || "http://localhost:8000";
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: "html",
  use: {
    baseURL: baseUrl,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.CI || process.env.SKIP_WEBSERVER
    ? undefined
    : {
        command: "poetry run owlclaw start --port 8000",
        cwd: repoRoot,
        url: "http://127.0.0.1:8000/healthz",
        reuseExistingServer: !process.env.CI,
        timeout: 30000,
      },
});
