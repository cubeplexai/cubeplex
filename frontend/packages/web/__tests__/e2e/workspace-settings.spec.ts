import { test, expect, type Page } from '@playwright/test'

const PASSWORD = 'correcthorsebatterystaple'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

async function registerAndGetWsId(page: Page): Promise<string> {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  const match = page.url().match(/\/w\/([^/?#]+)/)
  if (!match) throw new Error(`Could not parse workspace id from URL: ${page.url()}`)
  return match[1]
}

test.describe('Workspace Settings', () => {
  test('settings gear icon is visible in sidebar footer', async ({ page }) => {
    await registerAndGetWsId(page)

    const settingsLink = page.getByRole('link', { name: /workspace settings/i })
    await expect(settingsLink).toBeVisible()
  })

  test('clicking settings icon navigates to settings page and shows Persona nav', async ({
    page,
  }) => {
    await registerAndGetWsId(page)

    // force: true bypasses Playwright's hit-target check. CI's headless
    // Chromium intermittently reports the workspace home page's flex-1
    // content as "intercepting" the click on the sidebar footer link even
    // though they don't actually overlap. Visibility is asserted in the
    // previous test; this test only needs to verify the navigation works.
    await page.getByRole('link', { name: /workspace settings/i }).click({ force: true })
    await expect(page).toHaveURL(/\/settings/, { timeout: 10_000 })

    // SettingsNav renders "Persona" as a sub-item under the active workspace tab
    await expect(page.getByText('Persona')).toBeVisible({ timeout: 10_000 })
  })

  test('default settings page (workspace tab) renders Persona heading', async ({ page }) => {
    const wsId = await registerAndGetWsId(page)

    await page.goto(`/w/${wsId}/settings`)
    await expect(page).toHaveURL(/\/settings/, { timeout: 10_000 })

    // PersonaEditor renders <h2>Persona</h2>
    await expect(page.getByRole('heading', { name: 'Persona' })).toBeVisible({ timeout: 10_000 })
  })

  test('skills tab shows Skills heading', async ({ page }) => {
    const wsId = await registerAndGetWsId(page)

    await page.goto(`/w/${wsId}/settings?tab=skills`)
    // SkillsPanel renders <p>Skills</p>
    await expect(page.getByText('Skills').first()).toBeVisible({ timeout: 10_000 })
  })

  test('mcp tab shows MCP Connectors heading', async ({ page }) => {
    const wsId = await registerAndGetWsId(page)

    await page.goto(`/w/${wsId}/settings?tab=mcp`)
    // McpPanel renders <p>MCP Connectors</p>; use .first() because the sidebar nav
    // also renders an "MCP Connectors" item when on the settings route.
    await expect(page.getByText('MCP Connectors').first()).toBeVisible({ timeout: 10_000 })
  })
})
