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

test.describe('admin console skeleton', () => {
  test('registered user (auto-admin) sees 管理后台 popover entry and reaches /admin', async ({
    page,
    context,
  }) => {
    await registerAs(page, uniqueEmail())
    // Sidebar landmarked for accessibility
    await expect(page.getByRole('complementary', { name: /sidebar/i })).toBeVisible()

    // Open avatar popover and verify admin entry
    await page.getByRole('button', { name: /account menu/i }).click()
    const adminLink = page.getByRole('link', { name: '管理后台' })
    await expect(adminLink).toBeVisible()

    // Click opens /admin in a new tab (target=_blank)
    const pagePromise = context.waitForEvent('page')
    await adminLink.click()
    const adminPage = await pagePromise
    await adminPage.waitForLoadState()
    await expect(adminPage).toHaveURL(/\/admin(\/models)?/, { timeout: 10_000 })

    // Top bar shows product name + admin heading
    await expect(adminPage.getByRole('heading', { name: '管理后台' })).toBeVisible()

    // Sub-nav: 5 CE native items should be present
    const nav = adminPage.getByRole('navigation', { name: /admin sub-nav/i })
    await expect(nav).toBeVisible()
    for (const label of ['模型', 'Web 工具', '技能管理', 'MCP 连接器', '沙盒']) {
      await expect(nav.getByRole('link', { name: label })).toBeVisible()
    }
  })

  test('CE deployment: no external extension tabs render beyond the 5 natives', async ({
    page,
  }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin')
    // Wait for the loading state to pass (admin-me resolved)
    await expect(page.getByRole('heading', { name: '管理后台' })).toBeVisible({ timeout: 10_000 })
    const navLinks = page.getByRole('navigation', { name: /admin sub-nav/i }).getByRole('link')
    await expect(navLinks).toHaveCount(5)
  })

  test('unauthenticated /admin visit redirects to /login', async ({ context, page }) => {
    await context.clearCookies()
    await page.goto('/admin')
    await expect(page).toHaveURL(/\/login\?next=%2Fadmin/, { timeout: 10_000 })
  })
})
