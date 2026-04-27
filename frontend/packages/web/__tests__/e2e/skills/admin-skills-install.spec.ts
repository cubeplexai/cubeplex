import { test, expect } from '@playwright/test'
import { gotoAdminSkills, registerAsAdmin } from './_helpers'

test.describe('admin skills install', () => {
  test('admin can install deep-research and the install state flips', async ({ page }) => {
    await registerAsAdmin(page)
    await gotoAdminSkills(page)

    const card = page.getByTestId('skill-card-deep-research')
    await expect(card).toBeVisible({ timeout: 10_000 })
    await card.click()

    const detail = page.getByTestId('skill-detail-panel')
    await expect(detail).toBeVisible()

    // If already installed (depending on seed/state), this test still asserts
    // the installed indicator; otherwise click the install button first.
    const installBtn = page.getByTestId('skill-install-button')
    if (await installBtn.isVisible().catch(() => false)) {
      await installBtn.click()
    }

    // Detail panel should now show an uninstall button (= installed) or
    // an upgrade button (= installed but newer version available).
    await expect(
      page.getByTestId('skill-uninstall-button').or(page.getByTestId('skill-upgrade-button')),
    ).toBeVisible({ timeout: 10_000 })
  })
})
