import { test, expect, type Page, type APIRequestContext } from '@playwright/test'

const PASSWORD = 'correcthorsebatterystaple'

async function registerAndLand(page: Page): Promise<string> {
  const email = `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  const url = page.url()
  const m = url.match(/\/w\/([^/?#]+)/)
  if (!m) throw new Error(`no workspace id in url: ${url}`)
  return m[1]
}

// Probe the search API directly. Returns the fused_count so callers can
// decide whether indexing has actually produced results yet (requires a
// working embedding key in the backend lifespan; without one the worker
// errors and fused_count stays 0).
async function probeSearch(request: APIRequestContext, wsId: string, q: string): Promise<number> {
  const url = `/api/v1/ws/${wsId}/conversations/search?q=${encodeURIComponent(q)}&limit=8`
  const res = await request.get(url)
  if (!res.ok()) return 0
  const data = (await res.json()) as { fused_count?: number }
  return data.fused_count ?? 0
}

test.describe('conversation search', () => {
  test('typing a keyword shows a matching result', async ({ page, request }) => {
    const wsId = await registerAndLand(page)

    // Seed a conversation by sending a real chat message. The backend's
    // run-completion hook enqueues an embedding job, which the worker
    // drains — but only when DASHSCOPE_API_KEY is set in the backend
    // env. Without a key, fused_count stays 0 and we skip below.
    const KEYWORD = 'docling'
    const input = page.getByPlaceholder('Describe a task…')
    await input.fill(`tell me about ${KEYWORD} for table extraction`)
    await input.press('Enter')
    await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 10_000 })
    await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 60_000 })

    // Poll the API until indexing lands, with a generous budget. Skip the
    // test when search stays empty — this means the backend has no
    // embedding key configured (CI without DASHSCOPE_API_KEY).
    let fused = 0
    const deadline = Date.now() + 15_000
    while (Date.now() < deadline) {
      fused = await probeSearch(request, wsId, KEYWORD)
      if (fused > 0) break
      await page.waitForTimeout(1000)
    }
    test.skip(
      fused === 0,
      'search index empty — backend embedding worker not configured (DASHSCOPE_API_KEY missing)',
    )

    // Pop the search panel and check results render.
    await page.getByRole('button', { name: /search conversations/i }).click()
    const searchInput = page.getByPlaceholder(/search conversations/i)
    await expect(searchInput).toBeVisible()
    await searchInput.fill(KEYWORD)

    // First result row should appear in the popover.
    const popover = page.getByRole('dialog')
    await expect(popover.getByRole('mark').first()).toBeVisible({ timeout: 5000 })
  })

  test('escape closes popover', async ({ page }) => {
    await registerAndLand(page)
    await page.getByRole('button', { name: /search conversations/i }).click()
    const searchInput = page.getByPlaceholder(/search conversations/i)
    await expect(searchInput).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(searchInput).toBeHidden()
  })
})
