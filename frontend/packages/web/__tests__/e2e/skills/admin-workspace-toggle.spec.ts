import { test, expect } from '@playwright/test'
import { gotoAdminSkills, registerAsAdmin } from './_helpers'

test.describe('admin workspace binding toggle', () => {
  test('toggling Personal workspace persists across reload', async ({ page }) => {
    await registerAsAdmin(page)
    await gotoAdminSkills(page)

    // Pick the deep-research preinstalled skill.
    const card = page.getByTestId('skill-card-deep-research')
    await expect(card).toBeVisible({ timeout: 10_000 })
    await card.click()

    // Wait for the detail panel to fully load before inspecting buttons.
    await expect(page.getByTestId('skill-detail-panel')).toBeVisible({ timeout: 10_000 })

    // Ensure the org install exists (idempotent).
    const installBtn = page.getByTestId('skill-install-button')
    if (await installBtn.isVisible().catch(() => false)) {
      await installBtn.click()
      // Wait for the install transition.
      await expect(
        page.getByTestId('skill-uninstall-button').or(page.getByTestId('skill-upgrade-button')),
      ).toBeVisible({ timeout: 10_000 })
    }

    // Find the Personal workspace row.
    const personalCheckbox = page.getByTestId('ws-binding-checkbox-Personal')
    await expect(personalCheckbox).toBeVisible({ timeout: 10_000 })

    // Snapshot the current state, flip it, then reload.
    const wasChecked = await personalCheckbox.isChecked()
    await personalCheckbox.click()
    // Wait for any pending toggle to settle.
    await expect(personalCheckbox).toBeEnabled({ timeout: 10_000 })

    await page.reload()
    await expect(page.getByRole('heading', { name: '技能管理' })).toBeVisible()
    await page.getByTestId('skill-card-deep-research').click()

    const reloaded = page.getByTestId('ws-binding-checkbox-Personal')
    await expect(reloaded).toBeVisible({ timeout: 10_000 })
    if (wasChecked) {
      await expect(reloaded).not.toBeChecked()
    } else {
      await expect(reloaded).toBeChecked()
    }
  })
})
