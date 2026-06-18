import { test, expect } from '@playwright/test'

test.describe('admin console skeleton', () => {
  test('unauthenticated /admin visit redirects to /login', async ({ context, page }) => {
    await context.clearCookies()
    await page.goto('/admin')
    await expect(page).toHaveURL(/\/login\?next=%2Fadmin/, { timeout: 10_000 })
  })
})
