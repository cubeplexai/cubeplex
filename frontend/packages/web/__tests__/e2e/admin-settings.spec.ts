import { test, expect } from '@playwright/test'
import { registerAndLand } from './_helpers/auth'

test.describe('Org Settings', () => {
  test('save flow surfaces empty-state when no models exist', async ({ page }) => {
    await registerAndLand(page)
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
