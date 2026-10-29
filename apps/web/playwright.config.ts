import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "src/tests/e2e/playwright",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  expect: {
    timeout: 10000,
  },
  use: {
    baseURL: "http://127.0.0.1:4173",
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH,
    },
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm run build && npm run preview -- --host 127.0.0.1 --port 4173",
    env: {
      VITE_AUTH_AUTO_REDIRECT: "false",
    },
    url: "http://127.0.0.1:4173",
    reuseExistingServer: true,
    timeout: 120000,
  },
});
