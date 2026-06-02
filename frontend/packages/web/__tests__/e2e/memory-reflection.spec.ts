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

test('preference message triggers reflection and surfaces memory chip', async ({ page }) => {
  // Headroom: cold sandbox (~80s on first run) + main agent reply + detached
  // reflection LLM call (~5–15s after main run completes).
  test.setTimeout(180_000)
  await registerAndLand(page)

  const input = page.getByPlaceholder('How can I help you?')
  await input.fill('Please remember that I prefer concise, direct answers in our conversations.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//)

  // Wait for the main run to fully complete (loading indicator hidden).
  await expect(page.getByTestId('loading-indicator')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 120_000 })

  // After AgentEndEvent fires, the detached ReflectionRunner spawns a separate
  // LLM call which reads the last turn and decides whether to save memory.
  // The chip appears once the UserEvent reaches the frontend SSE channel.
  // Generous timeout because reflection LLM latency varies by provider.
  await expect(page.getByRole('button', { name: /已记住|已更新/ })).toBeVisible({
    timeout: 45_000,
  })
})
