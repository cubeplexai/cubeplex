import { test, expect } from '@playwright/test'
import { registerAndLand } from './_helpers/auth'

test.describe('M2 Model Management', () => {
  test('admin sees provider list with seeded system provider', async ({ page }) => {
    await registerAndLand(page)
    await page.goto('/admin/models')

    // Header
    await expect(page.getByRole('heading', { name: /Model Providers|模型提供商/ })).toBeVisible({
      timeout: 10_000,
    })

    // Seeded system provider (deepseek, from config.test.yaml) appears as a card
    await expect(page.getByTestId('provider-card-deepseek')).toBeVisible({ timeout: 10_000 })
  })

  test('admin can create, view, and delete a custom provider', async ({ page }) => {
    await registerAndLand(page)
    await page.goto('/admin/models')

    await expect(page.getByTestId('provider-card-deepseek')).toBeVisible({ timeout: 10_000 })

    // "Add provider" now opens the full-page wizard, not a dialog.
    await page.getByRole('button', { name: /Add provider|添加 Provider/ }).click()
    await expect(page).toHaveURL(/\/admin\/models\/new$/)

    // Step 1 (Preset): pick a vendor preset, then advance. Footer buttons sit
    // under the Next.js dev-overlay portal, which intercepts pointer events in
    // `pnpm dev` (even force-click dispatches at coordinates the portal owns), so
    // dispatch a DOM click straight to the button instead.
    await page.getByRole('button', { name: 'Anthropic', exact: true }).click()
    await page.getByRole('button', { name: /^(Next|下一步)$/ }).dispatchEvent('click')

    // Step 2 (Configure): name/base URL are seeded from the preset. Rename to a
    // unique value, drop auth to None so no real key is needed, then create.
    await page.getByLabel(/^(Name|名称)$/).fill('e2e-test-provider')
    await page.getByText(/^(None|无认证)$/).click()
    await page.getByTestId('provider-config-submit').click()

    // Provider is created at step 2; the wizard advances to the Models step where
    // "Finish later" appears. Clicking it (only present once a provider exists)
    // implicitly waits for the create to land, then returns to the list.
    await page.getByRole('button', { name: /Finish later|稍后完成/ }).dispatchEvent('click')
    await expect(page).toHaveURL(/\/admin\/models$/)

    // Card appears in the list; select it to render the detail panel.
    const card = page.getByTestId('provider-card-e2e-test-provider')
    await expect(card).toBeVisible({ timeout: 10_000 })
    await card.click()
    await expect(page.getByTestId('provider-detail-panel')).toBeVisible()

    // Delete provider via inline confirm (no native dialog).
    await page.getByTestId('provider-delete-button').click()
    await page.getByTestId('provider-delete-confirm').click()

    await expect(page.getByTestId('provider-card-e2e-test-provider')).toBeHidden({
      timeout: 5_000,
    })
  })

  test('provider create form offers only API key / None — no OAuth', async ({ page }) => {
    await registerAndLand(page)
    await page.goto('/admin/models')

    await expect(page.getByTestId('provider-card-deepseek')).toBeVisible({ timeout: 10_000 })

    await page.getByRole('button', { name: /Add provider|添加 Provider/ }).click()
    await expect(page).toHaveURL(/\/admin\/models\/new$/)

    await page.getByRole('button', { name: 'Anthropic', exact: true }).click()
    await page.getByRole('button', { name: /^(Next|下一步)$/ }).dispatchEvent('click')

    // Configure step: auth is exactly API key + None — OAuth is not offered.
    await expect(page.getByTestId('provider-config-submit')).toBeVisible()
    await expect(page.getByRole('radio')).toHaveCount(2)
    await expect(page.getByText('OAuth')).toHaveCount(0)
  })

  test('filter pills narrow provider list', async ({ page }) => {
    await registerAndLand(page)
    await page.goto('/admin/models')

    await expect(page.getByTestId('provider-card-deepseek')).toBeVisible({ timeout: 10_000 })

    // System filter keeps the seeded provider visible
    await page.getByRole('button', { name: /^(System|系统)$/ }).click()
    await expect(page.getByTestId('provider-card-deepseek')).toBeVisible()

    // Custom filter hides it (no custom providers yet)
    await page.getByRole('button', { name: /^(Custom|自建)$/ }).click()
    await expect(page.getByTestId('provider-card-deepseek')).toBeHidden()
  })
})
