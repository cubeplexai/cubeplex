import { test, expect } from '@playwright/test'
import { registerAndLand, uniqueEmail } from './_helpers/auth'

test.describe('Admin Insights page', () => {
  test('legacy /admin/cost redirects to /admin/insights', async ({ page }) => {
    await registerAndLand(page, uniqueEmail())
    await page.goto('/admin/cost')
    await expect(page).toHaveURL(/\/admin\/insights$/)
  })

  test('export CSV link returns csv content-type', async ({ page, request }) => {
    await registerAndLand(page, uniqueEmail())
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
})
