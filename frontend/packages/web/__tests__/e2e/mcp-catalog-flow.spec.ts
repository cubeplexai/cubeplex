/**
 * MCP catalog business-flow spec.
 *
 * Invariant: when an admin creates an org-custom template and distributes it
 * (enable_existing=true, auto_enroll=true), the workspace page shows it
 * enabled. When the admin then disables the template, the workspace page
 * shows it as disabled/removed.
 *
 * Setup is seeded via API (creates the template server-side without browser
 * overhead); assertions are through UI page.locator visibility, not JSON.
 *
 * Requires a running backend (CUBEPLEX_API_URL env) and frontend dev server.
 */

import { test, expect, type Page } from '@playwright/test'
import { registerAndLand } from './_helpers/auth'

const BACKEND_URL = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8091'

interface RegisterResult {
  wsId: string
  /** Session cookie header, extracted from browser context for direct API calls. */
  cookies: string
}

async function registerAndExtractSession(page: Page): Promise<RegisterResult> {
  const { wsId } = await registerAndLand(page)

  // Extract cookies from the browser context so we can make authenticated
  // direct API calls for setup/teardown without opening new pages.
  const allCookies = await page.context().cookies()
  const cookieHeader = allCookies.map((c) => `${c.name}=${c.value}`).join('; ')
  return { wsId, cookies: cookieHeader }
}

/**
 * Create an org-custom MCP template via the admin API.
 * Uses a static-token, no-auth server URL (the test doesn't need a live MCP
 * server — the template just needs to exist in the catalog).
 */
async function seedTemplate(cookies: string, name: string): Promise<string> {
  const res = await fetch(`${BACKEND_URL}/api/v1/admin/mcp/templates`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Cookie: cookies,
    },
    body: JSON.stringify({
      name,
      server_url: 'https://mcp-test-sink.internal/mcp',
      transport: 'streamable_http',
      auth_method: 'none',
      supported_auth_methods: ['none'],
      default_credential_policy: 'none',
    }),
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`seedTemplate failed ${res.status}: ${body}`)
  }
  const data = (await res.json()) as { template_id: string }
  return data.template_id
}

/**
 * Distribute a template to all workspaces (enable_existing=true, auto_enroll=true).
 */
async function distributeTemplate(cookies: string, templateId: string): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/v1/admin/mcp/templates/${templateId}/distribute`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Cookie: cookies,
    },
    body: JSON.stringify({ enable_existing: true, auto_enroll: true }),
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`distributeTemplate failed ${res.status}: ${body}`)
  }
}

/**
 * Disable a template in the org.
 */
async function disableTemplate(cookies: string, templateId: string): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/v1/admin/mcp/templates/${templateId}/disable`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      Cookie: cookies,
    },
    body: JSON.stringify({}),
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`disableTemplate failed ${res.status}: ${body}`)
  }
}

/**
 * Set workspace-level enabled state for a template.
 */
async function wsSetEnabled(
  cookies: string,
  wsId: string,
  templateId: string,
  enabled: boolean,
): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/v1/ws/${wsId}/mcp/templates/${templateId}/state`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      Cookie: cookies,
    },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`wsSetEnabled failed ${res.status}: ${body}`)
  }
}

test.describe('MCP catalog flow', () => {
  test('distributed template appears enabled in workspace; disabling in org removes it from active view', async ({
    page,
  }) => {
    // 1. Register a user (single-tenant → becomes org-admin automatically).
    const { wsId, cookies } = await registerAndExtractSession(page)

    // 2. Seed an org-custom template via API (avoid browser overhead for setup).
    const templateName = `e2e-mcp-${Date.now()}`
    const templateId = await seedTemplate(cookies, templateName)

    // 3. Distribute to existing workspaces (enable_existing=true, auto_enroll=true).
    await distributeTemplate(cookies, templateId)

    // 4. Navigate to workspace MCP settings page — template should be visible and enabled.
    await page.goto(`/w/${wsId}/mcp`)

    // Wait for the panel to load — the template should appear.
    await expect(page.getByTestId(`ws-catalog-row-${templateId}`)).toBeVisible({ timeout: 15_000 })

    // The toggle should be checked (enabled=true after distribute).
    const toggle = page.getByTestId(`ws-catalog-toggle-${templateId}`)
    await expect(toggle).toBeChecked({ timeout: 10_000 })

    // 5. Admin disables the template in the org via API.
    await disableTemplate(cookies, templateId)

    // 6. Reload workspace MCP page — backend excludes org-disabled templates entirely
    //    from the workspace catalog, so the row should disappear.
    await page.reload()
    await page.goto(`/w/${wsId}/mcp`)

    // The row should be hidden (org-disabled templates do not appear in the catalog).
    await expect(page.getByTestId(`ws-catalog-row-${templateId}`)).toBeHidden({
      timeout: 15_000,
    })
  })

  test('workspace can toggle individual template enabled state', async ({ page }) => {
    // 1. Register.
    const { wsId, cookies } = await registerAndExtractSession(page)

    // 2. Seed + distribute a template (starts enabled in workspace).
    const templateName = `e2e-mcp-toggle-${Date.now()}`
    const templateId = await seedTemplate(cookies, templateName)
    await distributeTemplate(cookies, templateId)

    // 3. Navigate to workspace MCP page.
    await page.goto(`/w/${wsId}/mcp`)
    await expect(page.getByTestId(`ws-catalog-row-${templateId}`)).toBeVisible({
      timeout: 15_000,
    })

    // 4. Disable it at the workspace level.
    await wsSetEnabled(cookies, wsId, templateId, false)

    // 5. Reload and check the filter works — switch to "Enabled" filter.
    await page.reload()
    await page.goto(`/w/${wsId}/mcp`)
    await expect(page.getByTestId(`ws-catalog-row-${templateId}`)).toBeVisible({
      timeout: 15_000,
    })

    // Click the "已启用"/"Enabled" filter chip — the row should disappear.
    await page.getByRole('button', { name: /已启用|^Enabled$/ }).click()

    await expect(page.getByTestId(`ws-catalog-row-${templateId}`)).toBeHidden({
      timeout: 5_000,
    })

    // Switch to "全部"/"All" — row reappears, showing disabled state.
    await page.getByRole('button', { name: /全部|All/ }).click()
    await expect(page.getByTestId(`ws-catalog-row-${templateId}`)).toBeVisible({
      timeout: 5_000,
    })
  })
})
