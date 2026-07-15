import { test, expect, type Page } from '@playwright/test'

const PASSWORD = 'correcthorsebatterystaple'

async function registerAndLand(page: Page): Promise<void> {
  const email = `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
}

async function startRun(page: Page, prompt: string): Promise<void> {
  const input = page.getByPlaceholder('Tell CubePlex what you want to get done…')
  await input.fill(prompt)
  await input.press('Enter')
  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//)
  await expect(page.getByTestId('loading-indicator')).toBeVisible({ timeout: 20_000 })
}

// A prompt that streams steadily for a clear mid-run window but still finishes
// well within the test budget — and is purely conversational so it won't pull
// in tools (the E2E env has no sandbox). The steer drains at the run's tail.
const STREAMY_PROMPT =
  'Tell me a slow, gentle bedtime story about a small robot exploring a ' +
  'quiet forest. At least 400 words. Do not use any tools — just write prose.'

test('steer shows a pending chip, then commits into the transcript at a stable position', async ({
  page,
}) => {
  test.setTimeout(240_000)
  await registerAndLand(page)
  await startRun(page, STREAMY_PROMPT)

  // Type a steer while the run is streaming.
  const input = page.getByPlaceholder('Tell CubePlex what you want to get done…')
  await input.fill('Actually, also say hello at the end.')
  await input.press('Enter')

  // Pending chip appears above the input (not yet in the transcript).
  const chip = page.getByTestId('pending-steer')
  await expect(chip).toBeVisible({ timeout: 5_000 })
  await expect(chip).toContainText('Actually, also say hello at the end.')

  // Once cubepi drains the steer, the chip disappears and the steer becomes a
  // real user message in the transcript.
  await expect(chip).toBeHidden({ timeout: 150_000 })
  const steerInTranscript = page
    .locator('[data-role="user"]')
    .filter({ hasText: 'Actually, also say hello at the end.' })
  await expect(steerInTranscript).toBeVisible()

  // Capture the ordered role sequence, then reload and assert it is unchanged
  // (the committed steer must not jump position on refresh).
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 150_000 })
  const rolesBefore = await page
    .locator('[data-role]')
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-role')))

  await page.reload()
  await expect(page.locator('[data-role="user"]').first()).toBeVisible({ timeout: 10_000 })
  const rolesAfter = await page
    .locator('[data-role]')
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-role')))
  expect(rolesAfter).toEqual(rolesBefore)
})

test('cancelling a pending steer removes the chip before it is injected', async ({ page }) => {
  test.setTimeout(240_000)
  await registerAndLand(page)
  await startRun(page, STREAMY_PROMPT)

  const input = page.getByPlaceholder('Tell CubePlex what you want to get done…')
  await input.fill('Never mind this instruction.')
  await input.press('Enter')

  const chip = page.getByTestId('pending-steer')
  await expect(chip).toBeVisible({ timeout: 5_000 })

  // Cancel before the agent drains it.
  await chip.getByRole('button', { name: /cancel/i }).click()
  await expect(chip).toBeHidden({ timeout: 5_000 })

  // It must never appear as a committed user message in the transcript.
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 150_000 })
  await expect(
    page.locator('[data-role="user"]').filter({ hasText: 'Never mind this instruction.' }),
  ).toHaveCount(0)
})
