import { defineConfig, devices } from '@playwright/test'

// Runs against a throwaway seeded server (scripts/e2e_server.py) using the Chrome already on this Mac.
const PORT = 8799
const chrome = { browserName: 'chromium' as const, channel: 'chrome' }

export default defineConfig({
  testDir: 'e2e',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, screenshot: 'only-on-failure' },
  webServer: {
    command: `cd .. && uv run python scripts/e2e_server.py --port ${PORT} --items ${process.env.E2E_ITEMS ?? 300}`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'iphone', use: { ...devices['iPhone 15'], ...chrome } },
    { name: 'pixel', use: { ...devices['Pixel 7'], ...chrome } },
    { name: 'desktop', use: { ...devices['Desktop Chrome'], ...chrome, viewport: { width: 1280, height: 800 } } },
  ],
})
