import { test, expect, type Page } from '@playwright/test'

/**
 * E2E for the install→auth handoff feature.
 *
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md
 *
 * Scope of this file:
 * - Verifies the WsAuthBand wiring doesn't regress the MCP tab page load.
 * - Verifies an admin (workspace owner = first-registered user) sees the
 *   "Connector templates" section in workspace settings.
 *
 * Deliberately out of scope (deferred to follow-up infra work):
 * - Full static install → save token → ready flow. Needs a seeded
 *   `auth_method='static'` template in the running dev DB. Backend covers
 *   the install/grant/auth_status state machine via tests/e2e/
 *   test_mcp_oauth_handoff.py; the frontend pure-function
 *   `computeAuthBandState` has unit coverage; the missing piece is a
 *   stable dev seed.
 * - Member-doesn't-see-templates: requires inviting a non-admin into a
 *   workspace, which has no existing test helper. Plan §5.1 still
 *   mandates the hide-template-list rule and Task 9 implements it; the
 *   gate keys off `useWorkspaceStore`'s role, which is unit-testable.
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

test.describe('MCP install→auth handoff', () => {
  test('admin sees Connector templates section', async ({ page }) => {
    const wsId = await registerAndGetWsId(page)
    await page.goto(`/w/${wsId}/settings?tab=mcp`)

    // The first registered user is the workspace owner (= admin), so the
    // template list section should render. Task 9 of the impl plan added
    // an admin-only gate; this asserts admins still see the section.
    await expect(page.getByText('MCP Connectors').first()).toBeVisible({
      timeout: 10_000,
    })
    await expect(page.getByText('Connector templates').first()).toBeVisible({
      timeout: 10_000,
    })
  })

  test('MCP tab page loads without runtime errors after WsAuthBand wiring', async ({ page }) => {
    const wsId = await registerAndGetWsId(page)

    const consoleErrors: string[] = []
    page.on('pageerror', (err) => consoleErrors.push(err.message))
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })

    await page.goto(`/w/${wsId}/settings?tab=mcp`)
    await expect(page.getByText('MCP Connectors').first()).toBeVisible({
      timeout: 10_000,
    })

    // No connectors yet for a fresh workspace, so the action band should
    // not be visible (it's only rendered when a connector is selected and
    // it's not in the 'hidden' state).
    await expect(page.getByText('Needs your credential')).toHaveCount(0)
    await expect(page.getByText('Waiting for authorization')).toHaveCount(0)

    // Page must mount cleanly — no runtime errors from the new wiring.
    expect(consoleErrors, consoleErrors.join('\n')).toEqual([])
  })
})
