import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './packages/web/__tests__/e2e',
  fullyParallel: false,
  workers: 1,
  retries: 1,
  timeout: 90_000,
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'pnpm --filter web dev',
    url: 'http://localhost:3000',
    reuseExistingServer: true,
    timeout: 30_000,
  },
})
