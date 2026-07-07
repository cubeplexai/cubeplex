import { test, expect } from '@playwright/test'

test.describe('i18n — language preference', () => {
  test('login page shows Chinese when NEXT_LOCALE cookie is zh', async ({ page }) => {
    await page.goto('/login')
    await page.evaluate(() => {
      document.cookie = 'NEXT_LOCALE=zh; path=/'
    })
    await page.reload()
    await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
    await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  })

  test('login page shows English when browser prefers en', async ({ page }) => {
    await page.setExtraHTTPHeaders({ 'Accept-Language': 'en-US,en;q=0.9' })
    await page.goto('/login')
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  })

  test('language switcher changes login UI and persists after reload', async ({ page }) => {
    await page.setExtraHTTPHeaders({ 'Accept-Language': 'en-US,en;q=0.9' })
    await page.goto('/login')
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()

    await page.getByRole('combobox', { name: 'Language' }).selectOption('zh')
    await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible({ timeout: 8_000 })

    await page.reload()
    await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()

    await page.getByRole('combobox', { name: '语言' }).selectOption('en')
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible({
      timeout: 8_000,
    })
  })
})
