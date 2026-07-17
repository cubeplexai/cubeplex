import { test, expect } from '@playwright/test'
import { registerAndLand } from './_helpers/auth'

test.describe('Org Settings', () => {
  test('model preset settings render the built-in tiers', async ({ page }) => {
    await registerAndLand(page)
    await page.goto('/admin/presets')

    await expect(page.getByTestId('tier-row-pro')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('tier-row-lite')).toBeVisible()
  })
})
