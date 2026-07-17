// Usage (from frontend/): node scripts/dev/capture-screens.mjs <stage-label>
// Captures key pages light+dark into .superpowers/screens/<stage-label>/
import { chromium } from '@playwright/test'
import { mkdirSync, existsSync } from 'node:fs'

const BASE = process.env.BASE_URL ?? 'http://127.0.0.1:3001'
const stage = process.argv[2] ?? 'adhoc'
const root = new URL('../../../', import.meta.url).pathname
const outDir = `${root}.superpowers/screens/${stage}/`
const stateFile = `${root}.superpowers/screens/.auth-state.json`
mkdirSync(outDir, { recursive: true })

const EMAIL = 'screens@cubeplex.dev'
const PASSWORD = 'Screens-Harness-2026'

const browser = await chromium.launch()
try {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    ...(existsSync(stateFile) ? { storageState: stateFile } : {}),
  })
  const page = await ctx.newPage()

  // establish session: storageState → login → register (first run, fresh DB)
  await page.goto(`${BASE}/`)
  if (!/\/w\//.test(page.url())) {
    await page.goto(`${BASE}/login`)
    await page.getByRole('textbox', { name: 'Email' }).fill(EMAIL)
    await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD)
    await page.getByRole('button', { name: /sign in/i }).click()
    // 'networkidle' never settles on pages with SSE/HMR connections
    await page.waitForTimeout(2500)
    if (!/\/w\//.test(page.url())) {
      await page.goto(`${BASE}/register`)
      await page.getByRole('textbox', { name: 'Email' }).fill(EMAIL)
      await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD)
      await page.getByRole('button', { name: /create account/i }).click()
      await page.waitForURL(/\/w\//)
    }
    await ctx.storageState({ path: stateFile })
  }
  const wsUrl = new URL(page.url()).pathname // /w/<wsId>

  const PAGES = [
    ['chat-home', wsUrl],
    ['ws-skills', `${wsUrl}/skills`],
    ['ws-settings', `${wsUrl}/settings?tab=workspace`],
    ['admin-models', '/admin/models'],
    ['admin-members', '/admin/members'],
    ['login-page', '/login'], // captured last: leaves session cookies intact
  ]

  for (const theme of ['light', 'dark']) {
    await page.evaluate((t) => localStorage.setItem('theme', t), theme)
    for (const [name, path] of PAGES) {
      await page.goto(`${BASE}${path}`, { waitUntil: 'load' })
      await page.waitForTimeout(900) // settle fonts/transitions; networkidle never fires with SSE/HMR
      await page.screenshot({ path: `${outDir}/${name}-${theme}.png` })
    }
  }
  console.log(`screens -> ${outDir}`)
} finally {
  await browser.close()
}
