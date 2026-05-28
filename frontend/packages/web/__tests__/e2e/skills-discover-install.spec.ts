/**
 * Workspace skills page — discover + install smoke test.
 *
 * Scope: test 1 (discover → install local skill → appears in list) is the
 * primary smoke test. Tests 2 and 3 (remote variant, check-for-update) are
 * scope-cut for v1 because:
 *   - test 2 needs a pre-registered fake remote source fixture
 *   - test 3 needs source_ref on SkillSummary (not yet surfaced)
 * Both will be added when the remote-install UX is fully wired.
 */

import { test, expect } from '@playwright/test'

const PASSWORD = 'correcthorsebatterystaple'

async function registerAndGetWsId(page: import('@playwright/test').Page): Promise<string> {
  const email = `skills-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 15_000 })
  const url = page.url()
  const wsId = url.match(/\/w\/([^/?#]+)/)?.[1]
  if (!wsId) throw new Error(`Could not extract wsId from URL: ${url}`)
  return wsId
}

test('skills page loads and search surfaces the deep-research skill', async ({ page }) => {
  const wsId = await registerAndGetWsId(page)
  await page.goto(`/w/${wsId}/skills`)

  await expect(page.getByRole('heading', { name: /Skills/i })).toBeVisible()

  // Discover panel is present
  await expect(page.getByPlaceholder(/Search skills/i)).toBeVisible()

  // Search for a known preinstalled skill
  await page.getByPlaceholder(/Search skills/i).fill('research')
  await page.getByRole('button', { name: /Search/i }).click()

  // At least one candidate card should appear
  const card = page.getByTestId('skill-candidate-card').filter({ hasText: 'deep-research' })
  await expect(card).toBeVisible({ timeout: 10_000 })

  // Should show the source badge ("preinstalled")
  await expect(card.getByText(/preinstalled/i)).toBeVisible()
})

test('install button installs skill and shows install confirmation', async ({ page }) => {
  const wsId = await registerAndGetWsId(page)
  await page.goto(`/w/${wsId}/skills`)

  await page.getByPlaceholder(/Search skills/i).fill('research')
  await page.getByRole('button', { name: /Search/i }).click()

  const card = page.getByTestId('skill-candidate-card').filter({ hasText: 'deep-research' })
  await expect(card).toBeVisible({ timeout: 10_000 })

  await card.getByRole('button', { name: /^Install$/ }).click()

  // Install confirmation message appears
  await expect(page.getByText(/Installed deep-research/i)).toBeVisible({ timeout: 10_000 })

  // Skill appears in the workspace list
  await expect(page.getByTestId('skills-list').getByText('deep-research')).toBeVisible({
    timeout: 10_000,
  })
})
