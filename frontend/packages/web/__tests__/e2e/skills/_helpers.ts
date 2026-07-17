import { expect, type Page } from '@playwright/test'
import { PASSWORD, registerAndLand, uniqueEmail } from '../_helpers/auth'

export { PASSWORD, uniqueEmail }

/**
 * Register a fresh account (auto-admin under M3 single-user-org bootstrap)
 * and land in the personal workspace. Returns the email used.
 */
export async function registerAsAdmin(page: Page): Promise<string> {
  const email = uniqueEmail()
  await registerAndLand(page, email)
  return email
}

/**
 * Navigate to the admin Skills tab, ensuring the page loads.
 */
export async function gotoAdminSkills(page: Page): Promise<void> {
  await page.goto('/admin/skills')
  await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible({
    timeout: 10_000,
  })
}
