import { test, expect, type Page } from '@playwright/test'

const PASSWORD = 'correcthorsebatterystaple'

async function registerAndLand(page: Page): Promise<string> {
  const email = `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  const url = new URL(page.url())
  const wsId = url.pathname.split('/')[2]
  return wsId
}

test('Memory Center: create + list + archive personal memory', async ({ page, request }) => {
  const wsId = await registerAndLand(page)

  // Seed a personal memory via the API (using the browser's session cookies).
  const apiBase = process.env.PLAYWRIGHT_API_BASE ?? page.url().split('/w/')[0]
  const cookies = await page.context().cookies()
  const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join('; ')
  const csrf = cookies.find((c) => c.name.startsWith('cubebox_csrf'))?.value ?? ''

  const seedRes = await request.post(`${apiBase}/api/v1/ws/${wsId}/memory`, {
    headers: { 'X-CSRF-Token': csrf, Cookie: cookieHeader, 'Content-Type': 'application/json' },
    data: { scope: 'personal', type: 'preference', content: 'E2E seeded preference' },
  })
  expect(seedRes.status()).toBe(201)

  await page.goto(`/w/${wsId}/memory`)
  await expect(page.getByText('E2E seeded preference')).toBeVisible({ timeout: 5_000 })
})
