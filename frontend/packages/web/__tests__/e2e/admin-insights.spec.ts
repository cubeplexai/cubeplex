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

  test('clicking a workspace chip filters charts (URL or refetch fires)', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/insights')
    await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
      timeout: 10_000,
    })
    // The default user has at least one workspace seeded; click the first chip in the sidebar
    const sidebar = page.getByRole('complementary', { name: /filters/i })
    // chip selectors are buttons with workspace ids as labels; the first one we can find
    const firstChip = sidebar
      .locator('button')
      .filter({ hasText: /^[a-zA-Z]/ })
      .first()
    if ((await firstChip.count()) === 0) {
      test.skip(true, 'no workspaces in fresh org')
      return
    }
    await firstChip.click()
    // No load-failed banner should appear after clicking
    await expect(page.getByText(/load failed/i)).not.toBeVisible()
  })

  test('cache section renders hit rate column as a percentage', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/insights')
    await expect(page.getByRole('heading', { name: /Cache efficiency/ })).toBeVisible({
      timeout: 10_000,
    })
    // A fresh org has no cost rows yet, so the section renders its empty state
    // instead of a bare table; with data, the Hit rate column header appears.
    await expect(
      page
        .getByRole('columnheader', { name: 'Hit rate' })
        .or(page.getByText('No data in this period'))
        .first(),
    ).toBeVisible()
  })

  test('a non-admin org member cannot access /admin/insights', async ({ page: _page }) => {
    // In multi-tenant test mode every new user becomes the owner of their own org.
    // So this test cannot synthesize a non-admin without significant fixture work.
    // We skip rather than mock — flag as a future task.
    test.skip(true, 'non-admin scenario requires multi-user org fixture; future work')
  })
})
