import { defineConfig, devices } from '@playwright/test'

// Each browser project gets its own freshly seeded server (scripts/e2e_server.py: temp home, mock S3,
// fake Apple Photos), so tests in one project can't disturb another. Uses the Chrome on this Mac.
const chrome = { browserName: 'chromium' as const, channel: 'chrome' }
const items = process.env.E2E_ITEMS ?? '300'
const PROJECTS = [
  { name: 'iphone', port: 8801, use: { ...devices['iPhone 15'], ...chrome } },
  { name: 'pixel', port: 8802, use: { ...devices['Pixel 7'], ...chrome } },
  { name: 'desktop', port: 8803, use: { ...devices['Desktop Chrome'], ...chrome, viewport: { width: 1280, height: 800 } } },
  // Safari's engine (WebKit) at iPhone size: closest to iOS Safari available on a Mac. Runs the on-device tests.
  { name: 'webkit-iphone', port: 8805, only: /local|offline/, use: { ...devices['iPhone 15'] } },
  // A phone reaching the Mac through `tailscale serve`: identified by the Tailscale login header.
  { name: 'tailnet-pixel', port: 8804, tailnet: true,
    use: { ...devices['Pixel 7'], ...chrome, extraHTTPHeaders: { 'Tailscale-User-Login': 'phone-user@example.com' } } },
]

export default defineConfig({
  testDir: 'e2e',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { screenshot: 'only-on-failure' },
  webServer: PROJECTS.map((p) => ({
    command: `cd .. && uv run python scripts/e2e_server.py --port ${p.port} --items ${items}`,
    url: `http://127.0.0.1:${p.port}/`,
    reuseExistingServer: false,
    timeout: 180_000,
  })),
  projects: PROJECTS.map((p) => ({
    name: p.name,
    ...(p.tailnet ? { testMatch: /tailnet/ } : 'only' in p && p.only ? { testMatch: p.only } : { testIgnore: /tailnet/ }),
    use: { ...p.use, baseURL: `http://127.0.0.1:${p.port}` },
  })),
})
