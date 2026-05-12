import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

async function registerAs(page: import('@playwright/test').Page, email: string): Promise<void> {
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
}

test.describe('Admin Insights page', () => {
  test('insights page renders KPI row and four sections', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/insights')
    await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
      timeout: 10_000,
    })
    await expect(page.getByText('Total cost')).toBeVisible()
    await expect(page.getByText('Cache hit rate')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'By workspace', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'By model', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'By user', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: /Cache efficiency/ })).toBeVisible()
  })

  test('legacy /admin/cost redirects to /admin/insights', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/cost')
    await expect(page).toHaveURL(/\/admin\/insights$/)
    await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
      timeout: 10_000,
    })
  })

  test('granularity toggle changes URL state', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/insights')
    await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
      timeout: 10_000,
    })
    await page.getByRole('button', { name: /^week$/ }).click()
    await expect(page.getByText(/load failed/i)).not.toBeVisible()
  })

  test('export CSV link returns csv content-type', async ({ page, request }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/insights')
    await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
      timeout: 10_000,
    })
    const cookies = await page.context().cookies()
    const cookieStr = cookies.map((c) => `${c.name}=${c.value}`).join('; ')
    const resp = await request.get('/api/v1/admin/cost/export.csv', {
      headers: { Cookie: cookieStr },
    })
    expect(resp.status()).toBe(200)
    expect(resp.headers()['content-type']).toContain('text/csv')
  })

  test('Insights nav item appears in admin sidebar', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin')
    await expect(page.getByRole('heading', { name: 'Admin' })).toBeVisible({ timeout: 10_000 })
    const nav = page.getByRole('navigation', { name: /admin sub-nav/i })
    await expect(nav.getByRole('link', { name: 'Insights' })).toBeVisible()
  })
})
