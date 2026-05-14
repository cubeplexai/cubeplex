import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

test('register → auto-login → land in personal workspace', async ({ page }) => {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  // Workspace home is the page that exposes the chat composer; assert via
  // the composer's testid rather than a marketing heading (the redesign
  // dropped the standalone "cubebox" h1 on this view).
  await expect(page.getByTestId('chat-input')).toBeVisible()
})

test('login → redirect to workspace; logout → redirect to login', async ({ page, context }) => {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\//)

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
