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

test('skills page loads with the deep-research skill in the local list', async ({ page }) => {
  const wsId = await registerAndGetWsId(page)
  await page.goto(`/w/${wsId}/skills`)

  await expect(page.getByRole('heading', { name: /Skills/i })).toBeVisible()

  // Discover panel is present (the toolbar searchbox runs the external-source
  // discovery; preinstalled skills already live in the local list below).
  await expect(page.getByRole('searchbox', { name: /Search skills/i })).toBeVisible()

  // The deep-research skill ships preinstalled and is auto-bound at registration,
  // so it appears in the workspace skills list without any user action.
  await expect(page.getByTestId('skills-list').getByText('deep-research')).toBeVisible({
    timeout: 10_000,
  })
})

test('preinstalled skill is auto-bound to a fresh workspace', async ({ page }) => {
  // Preinstalled skills (like deep-research) are auto-bound at registration —
  // they appear in the local list of a fresh workspace without any install step.
  const wsId = await registerAndGetWsId(page)
  await page.goto(`/w/${wsId}/skills`)

  // The skills list contains the preinstalled skill on first load.
  const localCard = page.getByTestId('skills-list').getByText('deep-research')
  await expect(localCard).toBeVisible({ timeout: 10_000 })

  // Clicking the card opens the detail panel for the skill.
  await localCard.click()
  await expect(page.getByRole('heading', { name: /deep-research/i })).toBeVisible({
    timeout: 5_000,
  })
})
