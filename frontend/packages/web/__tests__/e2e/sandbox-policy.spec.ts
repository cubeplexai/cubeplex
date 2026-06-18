import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

async function register(page: import('@playwright/test').Page): Promise<string> {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  return email
}

test.describe('Sandbox policy', () => {
  test('save flow: edit default image, save, see success', async ({ page }) => {
    await register(page)
    await page.goto('/admin/sandbox')

    const input = page.getByTestId('sandbox-policy-default-image')
    await expect(input).toBeVisible({ timeout: 10_000 })

    await input.fill('python:3.12')
    const save = page.getByTestId('sandbox-policy-save')
    await expect(save).toBeEnabled()
    await save.click()

    await expect(page.getByTestId('sandbox-policy-saved')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('sandbox-policy-save-error')).toHaveCount(0)
  })
})
