import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

async function register(page: import('@playwright/test').Page): Promise<string> {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  return email
}

test.describe('Sandbox policy', () => {
  test('admin nav routes to the sandbox policy page', async ({ page }) => {
    await register(page)
    await page.goto('/admin/models')

    await page.getByRole('link', { name: /Sandbox policy|沙盒策略/ }).click()
    await expect(page).toHaveURL(/\/admin\/sandbox$/)
    await expect(page.getByRole('heading', { name: /Sandbox policy|沙盒策略/ })).toBeVisible({
      timeout: 10_000,
    })
  })

  test('policy editor renders sections and save controls', async ({ page }) => {
    await register(page)
    await page.goto('/admin/sandbox')

    // Default image input visible
    await expect(page.getByTestId('sandbox-policy-default-image')).toBeVisible({
      timeout: 10_000,
    })

    // Save button present (disabled while clean)
    const save = page.getByTestId('sandbox-policy-save')
    await expect(save).toBeVisible()
    await expect(save).toBeDisabled()

    // Network + command sections rendered
    await expect(page.getByRole('heading', { name: /Network rules/ })).toBeVisible()
    await expect(page.getByRole('heading', { name: /Command rules/ })).toBeVisible()
  })

  test('typing into default image enables save, save succeeds', async ({ page }) => {
    await register(page)
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

test.describe('Workspace sandbox status page', () => {
  test('renders the status card with absent state for a fresh workspace', async ({ page }) => {
    await register(page)
    // URL after register is /w/{wsId}; navigate to sandbox under it.
    const url = new URL(page.url())
    await page.goto(`${url.pathname}/sandbox`)

    await expect(page.getByTestId('sandbox-status-card')).toBeVisible({ timeout: 10_000 })
    // Fresh workspace → no sandbox row yet.
    await expect(page.getByText(/Not running/)).toBeVisible()
  })
})
