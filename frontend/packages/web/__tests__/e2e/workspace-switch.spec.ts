import { test, expect } from '@playwright/test'
import { registerAndLand } from './_helpers/auth'

test('workspace switching isolates conversation lists', async ({ page }) => {
  await registerAndLand(page)
  const firstWsUrl = page.url()
  const firstWsId = firstWsUrl.split('/w/')[1]

  const input = page.getByPlaceholder('Tell CubePlex what you want to get done…')
  await input.fill('Hello in workspace 1')
  await input.press('Enter')
  // Generous timeout: this is the first API call against a just-created
  // workspace, the coldest path in the test.
  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 20_000 })
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
