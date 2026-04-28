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

test.describe('Admin cost page', () => {
  test('cost page renders heading and tables', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/cost')
    await expect(page.getByRole('heading', { name: '成本概览' })).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('按 Workspace')).toBeVisible()
    await expect(page.getByText('按 Model')).toBeVisible()
  })

  test('export CSV button returns csv content-type', async ({ page, request }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/cost')
    await expect(page.getByRole('heading', { name: '成本概览' })).toBeVisible({ timeout: 10_000 })

    // Get auth cookies from logged-in page
    const cookies = await page.context().cookies()
    const cookieStr = cookies.map((c) => `${c.name}=${c.value}`).join('; ')

    const resp = await request.get('/api/v1/admin/cost/export.csv', {
      headers: { Cookie: cookieStr },
    })
    expect(resp.status()).toBe(200)
    expect(resp.headers()['content-type']).toContain('text/csv')
  })

  test('cost nav item appears in admin sidebar', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin')
    await expect(page.getByRole('heading', { name: 'Admin' })).toBeVisible({ timeout: 10_000 })
    const nav = page.getByRole('navigation', { name: /admin sub-nav/i })
    await expect(nav.getByRole('link', { name: 'Cost' })).toBeVisible()
  })
})
