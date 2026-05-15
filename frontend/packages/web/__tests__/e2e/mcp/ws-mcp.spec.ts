import { expect, test } from '@playwright/test'

import { createOrgMcpServer, registerAndGetWorkspace } from './_helpers'

test.describe('Workspace MCP settings tab', () => {
  test('empty state shows when no connectors are enabled', async ({ page }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    await page.goto(`/w/${wsId}/settings?tab=mcp`)

    await expect(page.getByRole('heading', { name: 'MCP Connectors' })).toBeVisible()
    await expect(page.getByText('No MCP connectors yet')).toBeVisible()
  })

  test('org server enabled via admin shows in workspace settings', async ({ page }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    const server = await createOrgMcpServer(page, 'Visible Server')

    // Enable the server for the workspace via admin overrides API
    const csrfName = process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME ?? 'cubebox_csrf'
    const cookies = await page.context().cookies()
    const csrf = cookies.find((c) => c.name === csrfName)?.value ?? ''
    await page.request.put(`/api/v1/admin/mcp/servers/${server.id}/overrides`, {
      headers: { 'X-CSRF-Token': csrf },
      data: { workspace_id: wsId, enabled: true },
    })

    await page.goto(`/w/${wsId}/settings?tab=mcp`)
    await expect(page.getByText('Visible Server')).toBeVisible({ timeout: 10_000 })
  })

  test('old /integrations/mcp route returns 404', async ({ page }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    const resp = await page.goto(`/w/${wsId}/integrations/mcp`)
    expect(resp?.status()).toBe(404)
  })

  test('workspace MCP panel uses connector template labels and hides legacy copy', async ({
    page,
  }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    await page.goto(`/w/${wsId}/settings?tab=mcp`)

    await expect(page.getByText('Connector templates')).toBeVisible()
    await expect(page.getByText('Workspace state')).toBeVisible()
    await expect(page.getByText('Credential policy')).toBeVisible()
    await expect(page.getByText('Override')).toHaveCount(0)
    await expect(page.getByText('Catalog')).toHaveCount(0)
  })
})
