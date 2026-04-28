// Worktree-specific overrides — see next.config.ts for rationale.
import dotenv from 'dotenv'
import path from 'path'
dotenv.config({
  path: path.resolve(__dirname, '../.worktree.env'),
  override: false,
})

import { defineConfig, devices } from '@playwright/test'

const BASE_URL = process.env.BASE_URL ?? 'http://localhost:3000'

export default defineConfig({
  testDir: './packages/web/__tests__/e2e',
  fullyParallel: false,
  workers: 1,
  retries: 1,
  timeout: 90_000,
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'pnpm --filter web dev',
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 30_000,
  },
})
