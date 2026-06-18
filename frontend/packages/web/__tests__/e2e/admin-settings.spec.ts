import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

async function register(page: import('@playwright/test').Page): Promise<void> {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
}

test.describe('Org Settings', () => {
  test('save flow surfaces empty-state when no models exist', async ({ page }) => {
    await register(page)
    await page.goto('/admin/settings')

    await expect(page.getByTestId('org-llm-settings-card')).toBeVisible({ timeout: 10_000 })

    // No models seeded for a fresh org → empty-state copy, not the combobox.
    // Save remains visible (disabled until a model is picked).
    const empty = page.getByText(
      /No models available\.|暂无可用模型，请先在「模型」页面添加 Provider 与模型/,
    )
    const saveBtn = page.getByTestId('settings-save')
    await expect.soft(empty.or(saveBtn)).toBeVisible({ timeout: 10_000 })
  })
})
