import { test, expect } from '@playwright/test'
import { REAL_LLM_TAG, registerAndLand, skipWithoutRealLlm } from './_helpers/auth'

test(
  'preference message triggers reflection and surfaces memory chip',
  {
    tag: REAL_LLM_TAG,
  },
  async ({ page }) => {
    skipWithoutRealLlm()
    // Headroom: cold sandbox (~80s on first run) + main agent reply + detached
    // reflection LLM call (~5–15s after main run completes).
    test.setTimeout(180_000)
    await registerAndLand(page)

    const input = page.getByPlaceholder('Tell CubePlex what you want to get done…')
    await input.fill('Please remember that I prefer concise, direct answers in our conversations.')
    await input.press('Enter')

    await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//)

    // Wait for the main run to fully complete (loading indicator hidden).
    await expect(page.getByTestId('loading-indicator')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 120_000 })

    // MemoryUpdateChip is a permanent per-conversation count ("1 memories" /
    // "1 条记忆"). The main agent may save via tools, or the detached
    // ReflectionRunner publishes a UserEvent that refetches the count.
    await expect(page.getByRole('button', { name: /\d+\s+(memories|条记忆)/ })).toBeVisible({
      timeout: 45_000,
    })
  },
)
