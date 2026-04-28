import { test, expect } from '@playwright/test'
import { gotoAdminSkills, registerAsAdmin } from './_helpers'

test.describe('admin skills list', () => {
  test('preinstalled skills (deep-research) appear in the list', async ({ page }) => {
    await registerAsAdmin(page)
    await gotoAdminSkills(page)

    // The preinstalled `deep-research` skill ships with every org by default.
    const card = page.getByTestId('skill-card-deep-research')
    await expect(card).toBeVisible({ timeout: 10_000 })
    await expect(card).toContainText('deep-research')
  })

  test('search filter narrows the list', async ({ page }) => {
    await registerAsAdmin(page)
    await gotoAdminSkills(page)

    await page.getByRole('searchbox', { name: /search skills/i }).fill('deep')
    await expect(page.getByTestId('skill-card-deep-research')).toBeVisible({
      timeout: 5_000,
    })
  })
})
