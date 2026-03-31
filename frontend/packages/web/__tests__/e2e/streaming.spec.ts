import { test, expect } from '@playwright/test'

test('loading animation appears while streaming', async ({ page }) => {
  await page.goto('/')

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('Write a haiku about coding.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/conversations\//)

  // Loading indicator should appear
  await expect(page.getByTestId('loading-indicator')).toBeVisible({ timeout: 10_000 })

  // And disappear when done
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  // Final response should have meaningful content
  const assistantMsg = page.locator('[data-role="assistant"]')
  const text = await assistantMsg.textContent()
  expect(text!.length).toBeGreaterThan(20)
})

test('input is disabled while streaming', async ({ page }) => {
  await page.goto('/')

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('Write a short poem.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/conversations\//)

  // Input should be disabled while streaming
  await expect(page.getByPlaceholder('有什么可以帮你的？')).toBeDisabled({ timeout: 5_000 })

  // Wait for completion
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  // Input should be re-enabled after streaming completes
  await expect(page.getByPlaceholder('有什么可以帮你的？')).toBeEnabled()
})
