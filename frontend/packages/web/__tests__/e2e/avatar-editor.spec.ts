import { test, expect } from '@playwright/test'
import { PASSWORD, registerAndLand, uniqueEmail } from './_helpers/auth'

test.describe('avatar editor', () => {
  let email: string

  test.beforeAll(async ({ browser }) => {
    email = uniqueEmail('avatar-editor')
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    await registerAndLand(page, email)
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

    await page.getByRole('button', { name: 'Change profile picture' }).click()
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

    await page.getByRole('button', { name: 'Change profile picture' }).click()
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
