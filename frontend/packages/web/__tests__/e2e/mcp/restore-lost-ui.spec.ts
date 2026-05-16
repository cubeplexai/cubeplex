import { test, expect, type Page } from '@playwright/test'

/**
 * E2E for the lost-UI restoration features.
 *
 * Plan: docs/dev/plans/2026-05-16-mcp-restore-lost-ui.md (Task 11)
 *
 * Scope: lightweight — verify the new components mount without runtime
 * errors and that gating works for visibility. Heavier flow tests (full
 * Test Connection round-trip, real Promote, citation persistence) live
 * in backend E2E (tests/e2e/test_mcp_restore_lost_ui.py).
 */

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

test.describe('MCP lost-UI restoration', () => {
  test('admin /admin/mcp shows the + Add custom connector entry', async ({ page }) => {
    await registerAndGetWsId(page)
    await page.goto('/admin/mcp')

    // The +Add custom connector button is in the left rail under
    // Connector templates and is always visible to org admins (the
    // registering user is owner of the bootstrapped org).
    await expect(page.getByTestId('mcp-add-custom-connector')).toBeVisible({ timeout: 10_000 })
  })

  test('clicking + Add custom connector opens the custom install form', async ({ page }) => {
    await registerAndGetWsId(page)
    await page.goto('/admin/mcp')

    const addBtn = page.getByTestId('mcp-add-custom-connector')
    await expect(addBtn).toBeVisible({ timeout: 10_000 })
    await addBtn.click()
    await expect(page.getByTestId('mcp-admin-custom-form')).toBeVisible()
    // Confirm Test connection control is present.
    await expect(page.getByRole('button', { name: /test connection/i })).toBeVisible()
  })

  test('workspace settings → MCP loads without runtime errors', async ({ page }) => {
    const wsId = await registerAndGetWsId(page)
    await page.goto(`/w/${wsId}/settings?tab=mcp`)
    // The empty-state copy from the Tools tab — present once a connector
    // is selected. Until then the panel just renders headers without
    // throwing. Smoke-check that the page mounted (heading visible).
    await expect(page.getByRole('heading', { name: /MCP Connectors/i })).toBeVisible({
      timeout: 10_000,
    })
  })
})
