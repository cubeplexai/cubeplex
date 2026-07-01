import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

// The Playwright backend runs the dev config (password_policy=high, email
// verification OFF since email.backend=log). So register auto-verifies and the
// user lands on the onboarding wizard (multi_tenant no longer auto-bootstraps
// an org/workspace). The password must satisfy the high-strength rules.
const PASSWORD = 'Str0ng!Passw0rd'

// Complete the post-registration onboarding wizard (full mode: org + slug +
// workspace). Returns once landed in a workspace.
async function completeOnboarding(page: import('@playwright/test').Page): Promise<void> {
  await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
  // Full mode shows org name + slug + workspace name.
  await page.getByLabel(/organization name/i).fill(`Org ${Date.now()}`)
  // Slug auto-suggests from the org name; leave it.
  await page.getByLabel(/workspace name/i).fill('Personal')
  await page.getByRole('button', { name: /create organization and workspace/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 15_000 })
}

test('register → onboarding wizard → land in personal workspace', async ({ page }) => {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await completeOnboarding(page)
  await expect(page.getByRole('heading', { name: 'cubebox' })).toBeVisible()
})

test('login → redirect to workspace; logout → redirect to login', async ({ page, context }) => {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await completeOnboarding(page)

  await context.clearCookies()
  await page.goto('/login')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page).toHaveURL(/\/w\//, { timeout: 10_000 })

  await page.getByRole('button', { name: 'Account' }).click()
  await page.getByRole('button', { name: /sign out/i }).click()
  await expect(page).toHaveURL(/\/login$/)
})

test('unauthenticated visit to /workspaces redirects to /login with next param', async ({
  context,
  page,
}) => {
  await context.clearCookies()
  await page.goto('/workspaces')
  await expect(page).toHaveURL(/\/login\?next=%2Fworkspaces/)
})
