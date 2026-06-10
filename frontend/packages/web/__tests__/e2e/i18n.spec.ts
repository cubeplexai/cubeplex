import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

test.describe('i18n — language preference', () => {
  test('login page shows Chinese when NEXT_LOCALE cookie is zh', async ({ page }) => {
    await page.goto('/login')
    await page.evaluate(() => {
      document.cookie = 'NEXT_LOCALE=zh; path=/'
    })
    await page.reload()
    await expect(page.getByRole('heading', { name: '登录到 cubebox' })).toBeVisible()
    await expect(page.getByRole('button', { name: '登录' })).toBeVisible()
  })

  test('login page shows English when browser prefers en', async ({ page }) => {
    await page.setExtraHTTPHeaders({ 'Accept-Language': 'en-US,en;q=0.9' })
    await page.goto('/login')
    await expect(page.getByRole('heading', { name: 'Sign in to cubebox' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  })

  test('language switcher changes UI and persists after reload', async ({ page }) => {
    const email = uniqueEmail()

    // Register a new user — default language is 'en'
    await page.goto('/register')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: /create account/i }).click()
    await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })

    // Switch to Chinese via avatar popover
    await page.getByRole('button', { name: 'Account menu' }).click()
    await page.getByRole('button', { name: '中文' }).click()
    await expect(page.getByPlaceholder('描述一个任务…')).toBeVisible({ timeout: 8_000 })

    // Chinese persists after full reload
    await page.reload()
    await expect(page.getByPlaceholder('描述一个任务…')).toBeVisible()

    // Switch back to English (aria-label is now localized to Chinese here)
    await page.getByRole('button', { name: '账号菜单' }).click()
    await page.getByRole('button', { name: 'EN', exact: true }).click()
    await expect(page.getByPlaceholder('Describe a task…')).toBeVisible({ timeout: 8_000 })
  })
})
