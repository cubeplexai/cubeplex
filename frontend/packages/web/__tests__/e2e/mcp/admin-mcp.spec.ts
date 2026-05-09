import { expect, test } from '@playwright/test'

import { createOrgMcpServer, createWorkspace, registerAndGetWorkspace } from './_helpers'

test.describe('Admin MCP page', () => {
  test('OAuth option is disabled with Coming soon copy', async ({ page }) => {
    await registerAndGetWorkspace(page)
    await page.goto('/admin/mcp/new')

    await expect(page.getByRole('radio', { name: /OAuth/ })).toBeDisabled()
    await expect(page.getByText('Coming soon.')).toBeVisible()
  })

  test('saved credential plaintext is never rendered on admin detail', async ({ page }) => {
    await registerAndGetWorkspace(page)
    const server = await createOrgMcpServer(page, 'Secret UI Test')

    await page.goto(`/admin/mcp/${server.id}`)

    await expect(page.getByText('Secret UI Test')).toBeVisible()
    await expect(page.getByText('Org shared')).toBeVisible()
    await expect(page.getByText('test-secret-value')).toHaveCount(0)
  })

  test('per-row override toggle persists after reload', async ({ page }) => {
    // Org-wide servers default to enabled in every workspace; clicking the
    // switch writes a workspace_mcp_overrides row with enabled=false.
    // No batch "Save bindings" button — each toggle persists immediately.
    await registerAndGetWorkspace(page)
    await createWorkspace(page, 'workspace-A')
    const server = await createOrgMcpServer(page, 'Override UI Test')

    await page.goto(`/admin/mcp/${server.id}`)
    await page.getByRole('tab', { name: 'Workspaces' }).click()
    const toggle = page.getByRole('switch', { name: /workspace-A/i })
    await expect(toggle).toBeChecked()
    await toggle.click()
    // Per-row toggle persists synchronously; wait for the unchecked state.
    await expect(toggle).not.toBeChecked()

    await page.reload()
    await page.getByRole('tab', { name: 'Workspaces' }).click()

    await expect(page.getByRole('switch', { name: /workspace-A/i })).not.toBeChecked()
  })
})
