import { expect, type Page } from '@playwright/test'

export const PASSWORD = 'Str0ng!Passw0rd'

export function uniqueEmail(prefix = 'u'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}@example.com`
}

export async function completeOnboarding(page: Page): Promise<string> {
  const suffix = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
  await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
  await page.getByLabel(/organization name/i).fill(`Org ${suffix}`)
  await page.getByLabel(/workspace name/i).fill('Personal')
  await page.getByRole('button', { name: /create organization and workspace/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 15_000 })

  const wsId = page.url().match(/\/w\/([^/?#]+)/)?.[1]
  if (!wsId) throw new Error(`Could not extract workspace id from URL: ${page.url()}`)
  return wsId
}

export async function registerAndLand(
  page: Page,
  email = uniqueEmail(),
): Promise<{ email: string; wsId: string }> {
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  const wsId = await completeOnboarding(page)
  return { email, wsId }
}
