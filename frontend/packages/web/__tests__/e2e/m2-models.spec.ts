import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

test.describe('M2 Model Management', () => {
  test('admin can see provider list', async ({ page }) => {
    const email = uniqueEmail()
    await page.goto('/register')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: /create account/i }).click()
    await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })

    await page.goto('/admin/models')

    // Should see the providers heading
    await expect(page.getByText('Providers')).toBeVisible({ timeout: 10_000 })

    // System providers should appear (seeded from config.yaml)
    // The seed creates "cubebox" provider from config.yaml
    // Use button role to scope to the provider list (avoid banner brand match)
    await expect(page.getByRole('button', { name: /cubebox/ })).toBeVisible({ timeout: 10_000 })
  })

  test('admin can create and delete a provider', async ({ page }) => {
    const email = uniqueEmail()
    await page.goto('/register')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: /create account/i }).click()
    await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })

    await page.goto('/admin/models')

    // Wait for the provider list to load
    await expect(page.getByRole('button', { name: /cubebox/ })).toBeVisible({ timeout: 10_000 })

    // Click add provider button
    await page.getByRole('button', { name: '添加' }).click()

    // Wait for dialog
    await expect(page.getByTestId('provider-form-dialog')).toBeVisible()

    // Fill the form
    await page.getByLabel('名称').fill('e2e-test-provider')
    await page.getByLabel(/Base URL/).fill('https://example.com/api')

    // Select auth_type = none
    await page.getByText('无认证').click()

    // Save
    await page.getByRole('button', { name: '保存' }).click()

    // Provider should appear in the list (as a button element)
    await expect(page.getByRole('button', { name: /e2e-test-provider/ })).toBeVisible({
      timeout: 5_000,
    })

    // Click the provider button to see details
    await page.getByRole('button', { name: /e2e-test-provider/ }).click()

    // Should see detail view with action buttons
    await expect(page.getByTestId('provider-detail-panel')).toBeVisible()
    await expect(page.getByRole('button', { name: '编辑' })).toBeVisible()

    // Add a model
    await page
      .getByRole('button', { name: /添加模型/ })
      .first()
      .click()
    await expect(page.getByTestId('model-form-dialog')).toBeVisible()
    await page.getByLabel('Model ID').fill('e2e-test-model')
    await page.getByLabel('显示名称').fill('E2E Test Model')
    await page.getByRole('button', { name: '保存' }).last().click()

    // Model should appear
    await expect(page.getByText('e2e-test-model')).toBeVisible({ timeout: 5_000 })

    // Delete the provider (which cascades to model)
    page.once('dialog', (dialog) => dialog.accept())
    await page.getByRole('button', { name: '删除' }).click()

    // Provider should be gone from list
    await expect(page.getByRole('button', { name: /e2e-test-provider/ })).not.toBeVisible({
      timeout: 5_000,
    })
  })

  test('test connection button shows result', async ({ page }) => {
    const email = uniqueEmail()
    await page.goto('/register')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: /create account/i }).click()
    await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })

    await page.goto('/admin/models')

    // Wait for provider list to load
    await expect(page.getByRole('button', { name: /cubebox/ })).toBeVisible({ timeout: 10_000 })

    // Click add provider
    await page.getByRole('button', { name: '添加' }).click()
    await expect(page.getByTestId('provider-form-dialog')).toBeVisible()

    // Fill form
    await page.getByLabel('名称').fill('connection-test')
    await page.getByLabel(/Base URL/).fill('https://httpbin.org/post')
    await page.getByText('无认证').click()

    // Click test connection
    await page.getByRole('button', { name: '测试连接' }).click()

    // Should show a result (even if the test fails, the result component renders)
    await expect(page.locator('[data-testid="test-result"]')).toBeVisible({ timeout: 20_000 })
  })

  test('oauth option is disabled', async ({ page }) => {
    const email = uniqueEmail()
    await page.goto('/register')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: /create account/i }).click()
    await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })

    await page.goto('/admin/models')

    // Wait for provider list to load
    await expect(page.getByRole('button', { name: /cubebox/ })).toBeVisible({ timeout: 10_000 })

    // Open add provider dialog
    await page.getByRole('button', { name: '添加' }).click()
    await expect(page.getByTestId('provider-form-dialog')).toBeVisible()

    // OAuth 2.0 radio should be disabled
    const oauthRadio = page.getByRole('radio', { name: 'OAuth 2.0' })
    await expect(oauthRadio).toBeVisible()
    await expect(oauthRadio).toBeDisabled()
  })
})
