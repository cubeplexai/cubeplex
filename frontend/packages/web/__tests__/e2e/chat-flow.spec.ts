import { test, expect } from '@playwright/test'

test('can send a message and see a response', async ({ page }) => {
  await page.goto('/')

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('Say the word "hello" and nothing else.')
  await input.press('Enter')

  // Should navigate to conversation page
  await expect(page).toHaveURL(/\/conversations\//, { timeout: 10_000 })

  // User message should be visible in the main content area
  const main = page.getByRole('main')
  await expect(main.getByText('Say the word "hello" and nothing else.')).toBeVisible({
    timeout: 10_000,
  })

  // Wait for streaming to complete (loading indicator disappears)
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  // Assistant response should appear
  const assistantMsg = main.locator('[data-role="assistant"]')
  await expect(assistantMsg).toBeVisible()
  const text = await assistantMsg.textContent()
  expect(text!.trim().length).toBeGreaterThan(0)
})

test('conversation history persists after page reload', async ({ page }) => {
  await page.goto('/')

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('My favorite color is blue.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/conversations\//)
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  // Reload the page
  await page.reload()

  // History should still be visible in the main content area
  const main = page.getByRole('main')
  await expect(main.getByText('My favorite color is blue.')).toBeVisible({ timeout: 10_000 })
  await expect(main.locator('[data-role="assistant"]')).toBeVisible()
})
