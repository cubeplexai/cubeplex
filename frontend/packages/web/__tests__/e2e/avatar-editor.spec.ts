import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `avatar-editor-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

test.describe('avatar editor', () => {
  let email: string

  test.beforeAll(async ({ browser }) => {
    email = uniqueEmail()
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    await page.goto('/register')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: /create account/i }).click()
    await expect(page).toHaveURL(/\/w\//, { timeout: 10_000 })
    await ctx.close()
  })

  test('upload avatar persists across reload', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: /sign in/i }).click()
    await expect(page).toHaveURL(/\/w\//, { timeout: 10_000 })

    await page.goto('/settings/profile')
    await page.waitForSelector('text=Profile')

    const fileInput = page.locator('input[type="file"]')
    await fileInput.setInputFiles({
      name: 'test-avatar.png',
      mimeType: 'image/png',
      buffer: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
        'base64',
      ),
    })

    const avatarImg = page.locator('img').first()
    await expect(avatarImg).toHaveAttribute('src', /\.png/, { timeout: 15_000 })

    await page.reload()
    await page.waitForSelector('text=Profile')
    await expect(avatarImg).toHaveAttribute('src', /\.png/, { timeout: 15_000 })
  })

  test('shuffle picks a generated avatar that persists', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: /sign in/i }).click()
    await expect(page).toHaveURL(/\/w\//, { timeout: 10_000 })

    await page.goto('/settings/profile')
    await page.waitForSelector('text=Profile')

    await page.getByRole('button', { name: 'Shuffle' }).click()

    const galleryButtons = page.locator('section button').filter({ has: page.locator('img') })
    await expect(galleryButtons.first()).toBeVisible({ timeout: 5_000 })

    await galleryButtons.first().click()

    const avatarImg = page.locator('img').first()
    await expect(avatarImg).toHaveAttribute('src', /\.png/, { timeout: 15_000 })

    await page.reload()
    await page.waitForSelector('text=Profile')
    await expect(avatarImg).toHaveAttribute('src', /\.png/, { timeout: 15_000 })
  })
})
