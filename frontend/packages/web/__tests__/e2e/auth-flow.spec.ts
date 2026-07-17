import { test, expect } from '@playwright/test'
import { completeOnboarding, PASSWORD, uniqueEmail } from './_helpers/auth'

// The Playwright backend runs the dev config (password_policy=high, email
// verification OFF since email.backend=log). So register auto-verifies and the
// user lands on the onboarding wizard (multi_tenant no longer auto-bootstraps
// an org/workspace). The password must satisfy the high-strength rules.
test('register → onboarding wizard → land in personal workspace', async ({ page }) => {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await completeOnboarding(page)
  await expect(page.getByRole('heading', { name: 'CubePlex' })).toBeVisible()
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
