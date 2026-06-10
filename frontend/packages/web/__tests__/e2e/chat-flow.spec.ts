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

test('can send a message and see a response', async ({ page }) => {
  await registerAndLand(page)

  const input = page.getByPlaceholder('Describe a task…')
  await input.fill('Say the word "hello" and nothing else.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 10_000 })

  const main = page.getByRole('main')
  await expect(main.getByText('Say the word "hello" and nothing else.')).toBeVisible({
    timeout: 10_000,
  })

  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  const assistantMsg = main.locator('[data-role="assistant"]')
  await expect(assistantMsg).toBeVisible()
  const text = await assistantMsg.textContent()
  expect(text!.trim().length).toBeGreaterThan(0)
})

test('conversation history persists after page reload', async ({ page }) => {
  await registerAndLand(page)

  const input = page.getByPlaceholder('Describe a task…')
  await input.fill('My favorite color is blue.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//)
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  await page.reload()

  const main = page.getByRole('main')
  await expect(main.getByText('My favorite color is blue.')).toBeVisible({ timeout: 10_000 })
  // The agent may render multiple [data-role="assistant"] nodes per
  // run (thinking / tool calls + final response). The history-reload
  // assertion only cares that AT LEAST one survives the reload; use
  // .first() to keep the assertion robust against multi-step replies.
  await expect(main.locator('[data-role="assistant"]').first()).toBeVisible()
})
