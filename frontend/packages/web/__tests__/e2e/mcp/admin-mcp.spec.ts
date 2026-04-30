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

  test('bindings grid bulk save persists after reload', async ({ page }) => {
    await registerAndGetWorkspace(page)
    await createWorkspace(page, 'workspace-A')
    const server = await createOrgMcpServer(page, 'Binding UI Test')

    await page.goto(`/admin/mcp/${server.id}`)
    await page.getByRole('tab', { name: 'Workspaces' }).click()
    await page.getByRole('switch', { name: /workspace-A/i }).click()
    await page.getByRole('button', { name: 'Save bindings' }).click()
    await page.reload()
    await page.getByRole('tab', { name: 'Workspaces' }).click()

    await expect(page.getByRole('switch', { name: /workspace-A/i })).toBeChecked()
  })
})
