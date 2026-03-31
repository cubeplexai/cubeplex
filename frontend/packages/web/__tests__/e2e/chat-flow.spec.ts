import { test, expect } from '@playwright/test'

test('can send a message and see a response', async ({ page }) => {
  await page.goto('/')

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('Say the word "hello" and nothing else.')
  await input.press('Enter')

  // Should navigate to conversation page
  await expect(page).toHaveURL(/\/conversations\//, { timeout: 10_000 })

  // User message should be visible
  await expect(page.getByText('Say the word "hello" and nothing else.')).toBeVisible()

  // Wait for streaming to complete (loading dots disappear)
  await expect(page.locator('.animate-bounce').first()).toBeHidden({ timeout: 30_000 })

  // Assistant response should appear
  const assistantMsg = page.locator('[data-role="assistant"]')
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
  await expect(page.locator('.animate-bounce').first()).toBeHidden({ timeout: 30_000 })

  // Reload the page
  await page.reload()

  // History should still be visible
  await expect(page.getByText('My favorite color is blue.')).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('[data-role="assistant"]')).toBeVisible()
})
