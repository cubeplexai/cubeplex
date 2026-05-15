/**
 * E2E: Citation mapping tab in the admin MCP detail panel.
 *
 * Seeding gap: `tools_cache` can only be written via direct DB access
 * (the only API path is `refresh-tools`, which requires a live MCP server).
 * Tests that need a non-empty tool list are marked `.skip` and documented
 * below. The backend E2E in `tests/e2e/test_tool_citations_routes.py`
 * covers the full edit/save/persist cycle with DB seeding.
 */
import { expect, test } from '@playwright/test'

import { createOrgMcpServer, registerAndGetWorkspace } from './_helpers'

async function csrf(page: import('@playwright/test').Page): Promise<string> {
  const cookies = await page.context().cookies()
  const name = process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME ?? 'cubebox_csrf'
  return cookies.find((cookie) => cookie.name === name)?.value ?? ''
}

/**
 * Enable an org-wide MCP server for a specific workspace so that the
 * workspace-scoped tool-citations endpoint can see it.
 */
async function enableServerForWorkspace(
  page: import('@playwright/test').Page,
  serverId: string,
  wsId: string,
): Promise<void> {
  const token = await csrf(page)
  const resp = await page.request.put(`/api/v1/admin/mcp/servers/${serverId}/overrides`, {
    headers: { 'X-CSRF-Token': token },
    data: { workspace_id: wsId, enabled: true },
  })
  if (!resp.ok()) {
    throw new Error(`enable override failed: ${resp.status()} ${await resp.text()}`)
  }
}

/**
 * Navigate to the admin MCP page and open the named server's detail panel.
 */
async function openServerDetail(
  page: import('@playwright/test').Page,
  serverName: string,
): Promise<void> {
  await page.goto('/admin/mcp')
  await expect(page.getByRole('heading', { name: 'MCP Connectors' })).toBeVisible()

  // Filter to Custom to narrow the list, then click the server card
  const filterGroup = page.getByRole('group', { name: /filter/i })
  await filterGroup.getByRole('button', { name: 'Custom', exact: true }).click()

  const card = page.getByRole('button', { name: new RegExp(serverName, 'i') })
  await expect(card).toBeVisible({ timeout: 10_000 })
  await card.click()

  await expect(page.getByTestId('mcp-admin-detail-panel')).toBeVisible()
}

test.describe('Citation mapping tab', () => {
  test('Citation mapping tab is visible and loads without error', async ({ page }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    const { id: serverId } = await createOrgMcpServer(page, 'CitTab-Visibility')
    await enableServerForWorkspace(page, serverId, wsId)

    await openServerDetail(page, 'CitTab-Visibility')

    // The Citation mapping tab trigger must be visible in the tab bar
    const citTab = page.getByRole('tab', { name: /citation mapping/i })
    await expect(citTab).toBeVisible()

    // Click the tab
    await citTab.click()

    // Loading state resolves (the i18n loading text disappears)
    await expect(page.getByText('Loading citation mappings…')).toHaveCount(0, { timeout: 8_000 })

    // No error message shown (error renders via text-destructive but has no test-id;
    // we verify the panel loaded correctly by confirming the save button is present)
    const saveBtn = page.getByRole('button', { name: /save changes/i })
    await expect(saveBtn).toBeVisible()
    // Save button is disabled when there are no unsaved changes
    await expect(saveBtn).toBeDisabled()
  })

  test('Citation mapping tab shows empty tool list when tools_cache is empty', async ({ page }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    const { id: serverId } = await createOrgMcpServer(page, 'CitTab-EmptyCache')
    await enableServerForWorkspace(page, serverId, wsId)

    await openServerDetail(page, 'CitTab-EmptyCache')
    await page.getByRole('tab', { name: /citation mapping/i }).click()

    // Wait for loading to resolve
    await expect(page.getByText('Loading citation mappings…')).toHaveCount(0, { timeout: 8_000 })

    // The left panel renders but is empty — no tool-select buttons
    // Org server with no tools yet: tools_cache = [], so no tool buttons appear
    // (Status icons are aria-hidden and not part of accessible names)
    const toolButtons = page.locator('aside button[aria-pressed]')
    await expect(toolButtons).toHaveCount(0)
  })

  // ---------------------------------------------------------------------------
  // Skipped: these tests require tools_cache to be seeded with known tool names.
  // tools_cache is populated by refresh-tools (which calls a real MCP server)
  // or by direct DB write. Neither is available in the frontend E2E environment.
  //
  // The equivalent flows are exercised end-to-end in:
  //   backend/tests/e2e/test_tool_citations_routes.py
  // ---------------------------------------------------------------------------

  test.skip('edit → save → reload persists changes', async ({ page }) => {
    // BLOCKED: needs tools_cache seeded via DB before test can select a tool
    // and submit a PATCH. The PATCH endpoint validates tool names against
    // tools_cache, so an empty cache means no tool can be saved.
    //
    // To unblock: add a test-only admin endpoint that writes tools_cache
    // directly, or expose it as a field on the admin server PATCH schema.
    const { wsId } = await registerAndGetWorkspace(page)
    const { id: serverId } = await createOrgMcpServer(page, 'CitTab-EditSave')
    await enableServerForWorkspace(page, serverId, wsId)

    await openServerDetail(page, 'CitTab-EditSave')
    await page.getByRole('tab', { name: /citation mapping/i }).click()
    await expect(page.getByText('Loading citation mappings…')).toHaveCount(0, { timeout: 8_000 })

    // At this point tools_cache is [] so no tool button exists to click.
    // The rest of the test (click tool, fill Source type, Save, reload, verify)
    // cannot proceed without a populated tools_cache.
  })

  test.skip('dirty guard blocks tool switch with unsaved changes', async ({ page }) => {
    // BLOCKED: same tools_cache seeding limitation as above.
    //
    // Intended flow:
    //   1. Click a tool (e.g. web_search) — requires tools_cache to have it
    //   2. Edit Source type to a dirty value
    //   3. page.once('dialog', d => d.dismiss()) — auto-dismiss the confirm
    //   4. Click a different tool
    //   5. Assert Source type still shows the dirty value (switch was cancelled)
    const { wsId } = await registerAndGetWorkspace(page)
    const { id: serverId } = await createOrgMcpServer(page, 'CitTab-DirtyGuard')
    await enableServerForWorkspace(page, serverId, wsId)

    await openServerDetail(page, 'CitTab-DirtyGuard')
    await page.getByRole('tab', { name: /citation mapping/i }).click()
    await expect(page.getByText('Loading citation mappings…')).toHaveCount(0, { timeout: 8_000 })
    // Cannot proceed: no tools in the list to interact with.
  })
})
