// Throwaway local config (never committed): the installed headless shell, e2e or e2e-local specs,
// optionally a preview server sending production's CSP (CSP=1 uses vite.csp.config.ts).
import { defineConfig, devices } from "@playwright/test";

const PORT = 4173;
const shell = `${process.env.HOME}/.cache/ms-playwright/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell`;

export default defineConfig({
  testDir: process.env.PW_DIR ?? "e2e",
  workers: 1,
  fullyParallel: false,
  reporter: "list",
  timeout: 120_000,
  use: {
    baseURL: `http://localhost:${PORT}`,
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
    launchOptions: { executablePath: shell },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, launchOptions: { executablePath: shell } } }],
  webServer: {
    command: process.env.CSP ? `npx vite preview --config vite.csp.config.ts --port ${PORT} --strictPort` : `npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
