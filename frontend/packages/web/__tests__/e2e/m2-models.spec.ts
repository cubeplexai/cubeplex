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

test.describe('M2 Model Management', () => {
  test('admin sees provider list with seeded system provider', async ({ page }) => {
    await register(page)
    await page.goto('/admin/models')

    // Header
    await expect(page.getByRole('heading', { name: /Models|模型/ })).toBeVisible({
      timeout: 10_000,
    })

    // Seeded "cubebox" system provider appears as a provider card
    await expect(page.getByTestId('provider-card-cubebox')).toBeVisible({ timeout: 10_000 })
  })

  test('admin can create, view, and delete a custom provider', async ({ page }) => {
    await register(page)
    await page.goto('/admin/models')

    await expect(page.getByTestId('provider-card-cubebox')).toBeVisible({ timeout: 10_000 })

    // Open create dialog from toolbar
    await page.getByRole('button', { name: /Add provider|添加 Provider/ }).click()
    await expect(page.getByTestId('provider-form-dialog')).toBeVisible()

    // Fill the form (i18n agnostic — match by visible label fragment)
    await page.getByLabel(/^(Name|名称)$/).fill('e2e-test-provider')
    await page.getByLabel(/Base URL/).fill('https://example.com/api')
    await page.getByText(/^(None|无认证)$/).click()
    await page.getByRole('button', { name: /^(Save|保存)$/ }).click()

    // Card appears in the list and gets selected automatically
    const card = page.getByTestId('provider-card-e2e-test-provider')
    await expect(card).toBeVisible({ timeout: 5_000 })

    // Detail panel renders
    await expect(page.getByTestId('provider-detail-panel')).toBeVisible()

    // Add a model
    await page
      .getByRole('button', { name: /Add model|添加模型/ })
      .first()
      .click()
    await expect(page.getByTestId('model-form-dialog')).toBeVisible()
    await page.getByLabel(/Model ID/).fill('e2e-test-model')
    await page.getByLabel(/Display name|显示名称/).fill('E2E Test Model')
    await page.getByRole('button', { name: /^(Save|保存)$/ }).click()
    await expect(page.getByTestId('model-row-e2e-test-model')).toBeVisible({ timeout: 5_000 })

    // Delete provider via inline confirm (no native dialog)
    await page.getByTestId('provider-delete-button').click()
    await page.getByTestId('provider-delete-confirm').click()

    await expect(page.getByTestId('provider-card-e2e-test-provider')).toBeHidden({
      timeout: 5_000,
    })
  })

  test('oauth auth option is disabled in create dialog', async ({ page }) => {
    await register(page)
    await page.goto('/admin/models')

    await expect(page.getByTestId('provider-card-cubebox')).toBeVisible({ timeout: 10_000 })

    await page.getByRole('button', { name: /Add provider|添加 Provider/ }).click()
    await expect(page.getByTestId('provider-form-dialog')).toBeVisible()

    const oauthRadio = page.getByRole('radio', { name: /OAuth/ })
    await expect(oauthRadio).toBeVisible()
    await expect(oauthRadio).toBeDisabled()
  })

  test('filter pills narrow provider list', async ({ page }) => {
    await register(page)
    await page.goto('/admin/models')

    await expect(page.getByTestId('provider-card-cubebox')).toBeVisible({ timeout: 10_000 })

    // System filter keeps the seeded provider visible
    await page.getByRole('button', { name: /^(System|系统)$/ }).click()
    await expect(page.getByTestId('provider-card-cubebox')).toBeVisible()

    // Custom filter hides it (no custom providers yet)
    await page.getByRole('button', { name: /^(Custom|自建)$/ }).click()
    await expect(page.getByTestId('provider-card-cubebox')).toBeHidden()
  })
})
