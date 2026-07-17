import { test, expect } from '@playwright/test'
import { registerAndLand } from './_helpers/auth'

test.describe('Sandbox policy', () => {
  test('save flow: edit default image, save, see success', async ({ page }) => {
    await registerAndLand(page)
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
