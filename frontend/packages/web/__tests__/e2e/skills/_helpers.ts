import { expect, type Page } from '@playwright/test'

export const PASSWORD = 'correcthorsebatterystaple'

export function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

/**
 * Register a fresh account (auto-admin under M3 single-user-org bootstrap)
 * and land in the personal workspace. Returns the email used.
 */
export async function registerAsAdmin(page: Page): Promise<string> {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
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
