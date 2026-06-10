import { test, expect, type Page } from '@playwright/test'

const PASSWORD = 'correcthorsebatterystaple'

async function registerAndLand(page: Page): Promise<void> {
  const email = `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
}

test('loading animation appears while streaming', async ({ page }) => {
  // Cold sandbox provisioning for a fresh user can take ~80s in CI; raise the
  // per-test cap above the default 90s so the run can finish before timeout.
  test.setTimeout(150_000)
  await registerAndLand(page)

  const input = page.getByPlaceholder('Describe a task…')
  await input.fill('Write a haiku about coding.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//)

  await expect(page.getByTestId('loading-indicator')).toBeVisible({ timeout: 10_000 })
  // Cold sandbox provisioning for a fresh user can take ~80s in CI before the
  // run completes and the indicator hides; allow generous headroom over that.
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 120_000 })

  const assistantMsg = page.locator('[data-role="assistant"]')
  const text = await assistantMsg.textContent()
  expect(text!.length).toBeGreaterThan(20)
})

test('input stays editable while streaming (so the user can steer)', async ({ page }) => {
  // Same cold-sandbox headroom as above (fresh user → ~80s provisioning).
  test.setTimeout(150_000)
  await registerAndLand(page)

  const input = page.getByPlaceholder('Describe a task…')
  await input.fill('Write a short poem.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//)

  // While the run streams, the composer must remain enabled — steering needs
  // the user to type mid-run. (Previously the box was locked during streaming.)
  await expect(page.getByTestId('loading-indicator')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByPlaceholder('Describe a task…')).toBeEnabled()

  // Same cold-sandbox headroom as the test above (fresh user → ~80s provisioning).
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 120_000 })

  await expect(page.getByPlaceholder('Describe a task…')).toBeEnabled()
})
