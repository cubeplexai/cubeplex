import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

test('workspace switching isolates conversation lists', async ({ page }) => {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  const firstWsUrl = page.url()
  const firstWsId = firstWsUrl.split('/w/')[1]

  const input = page.getByPlaceholder('Describe a task…')
  await input.fill('Hello in workspace 1')
  await input.press('Enter')
  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 10_000 })
  const convInWs1Url = page.url()

  await page.goto('/workspaces')
  await expect(page.getByRole('link', { name: 'Open' })).toBeVisible({ timeout: 10_000 })
  await page.getByPlaceholder('e.g. Side project').fill('Side')
  await page.getByRole('button', { name: /create workspace/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/)
  const secondWsUrl = page.url()
  const secondWsId = secondWsUrl.split('/w/')[1]
  expect(secondWsId).not.toBe(firstWsId)

  const wrongUrl = convInWs1Url.replace(`/w/${firstWsId}/`, `/w/${secondWsId}/`)
  await page.goto(wrongUrl)
  await expect(page.getByText(/conversation not found/i)).toBeVisible({ timeout: 10_000 })
})
