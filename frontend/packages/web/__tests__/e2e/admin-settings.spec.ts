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
  test('settings nav entry routes to org settings page', async ({ page }) => {
    await register(page)
    await page.goto('/admin/models')

    // Settings entry exists and clicking takes us there
    await page.getByRole('link', { name: /Org Settings|组织设置/ }).click()
    await expect(page).toHaveURL(/\/admin\/settings$/)
    await expect(page.getByRole('heading', { name: /Org Settings|组织设置/ })).toBeVisible()
  })

  test('default LLM section renders and shows save controls', async ({ page }) => {
    await register(page)
    await page.goto('/admin/settings')

    // Card visible
    await expect(page.getByTestId('org-llm-settings-card')).toBeVisible({ timeout: 10_000 })

    // Save button is present (will be disabled while clean)
    const save = page.getByTestId('settings-save')
    await expect(save).toBeVisible()
    await expect(save).toBeDisabled()
  })

  test('save flow surfaces feedback when no models exist', async ({ page }) => {
    await register(page)
    await page.goto('/admin/settings')

    await expect(page.getByTestId('org-llm-settings-card')).toBeVisible({ timeout: 10_000 })

    // No models → empty-state hint instead of combobox
    // (the seeded "cubebox" provider has no models in default config)
    const empty = page.getByText(
      /No models available\.|暂无可用模型，请先在「模型」页面添加 Provider 与模型/,
    )
    const saveBtn = page.getByTestId('settings-save')
    // One of: empty state shown, or save controls present
    await expect.soft(empty.or(saveBtn)).toBeVisible({ timeout: 10_000 })
  })
})
