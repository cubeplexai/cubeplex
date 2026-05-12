import { expect, test } from '@playwright/test'

import { createOrgMcpServer, createWorkspace, registerAndGetWorkspace } from './_helpers'

function uid(): string {
  return Math.random().toString(36).slice(2, 8)
}

test.describe('Admin MCP page', () => {
  test('master-detail layout renders sidebar and placeholder', async ({ page }) => {
    await registerAndGetWorkspace(page)
    await page.goto('/admin/mcp')

    await expect(page.getByRole('heading', { name: 'MCP Connectors' })).toBeVisible()
    await expect(page.getByRole('searchbox', { name: /search/i })).toBeVisible()
    const filterGroup = page.getByRole('group', { name: /filter/i })
    await expect(filterGroup.getByRole('button', { name: 'All', exact: true })).toBeVisible()
    await expect(filterGroup.getByRole('button', { name: 'Installed', exact: true })).toBeVisible()
    await expect(filterGroup.getByRole('button', { name: 'Available', exact: true })).toBeVisible()
    await expect(filterGroup.getByRole('button', { name: 'Custom', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Add Custom' })).toBeVisible()
    await expect(page.getByText('Select a connector to view details')).toBeVisible()
  })

  test('installed custom server appears in sidebar and shows detail panel', async ({ page }) => {
    const tag = uid()
    await registerAndGetWorkspace(page)
    await createOrgMcpServer(page, `Detail-${tag}`)

    await page.goto('/admin/mcp')
    await expect(page.getByRole('heading', { name: 'MCP Connectors' })).toBeVisible()

    const filterGroup = page.getByRole('group', { name: /filter/i })
    await filterGroup.getByRole('button', { name: 'Custom', exact: true }).click()
    const card = page.getByRole('button', { name: new RegExp(`Detail-${tag}`, 'i') })
    await expect(card).toBeVisible()
    await card.click()

    const detail = page.getByTestId('mcp-admin-detail-panel')
    await expect(detail).toBeVisible()
    await expect(detail.getByRole('heading', { name: `Detail-${tag}` })).toBeVisible()
    await expect(detail.getByText(/127\.0\.0\.1/)).toBeVisible()
  })

  test('saved credential plaintext is never rendered on admin detail', async ({ page }) => {
    const tag = uid()
    await registerAndGetWorkspace(page)
    await createOrgMcpServer(page, `Secret-${tag}`)

    await page.goto('/admin/mcp')
    const filterGroup = page.getByRole('group', { name: /filter/i })
    await filterGroup.getByRole('button', { name: 'Custom', exact: true }).click()
    await page.getByRole('button', { name: new RegExp(`Secret-${tag}`, 'i') }).click()

    const detail = page.getByTestId('mcp-admin-detail-panel')
    await expect(detail).toBeVisible()
    await expect(detail.getByRole('heading', { name: `Secret-${tag}` })).toBeVisible()
    await expect(page.getByText('test-secret-value')).toHaveCount(0)
  })

  test('per-workspace override toggle persists after reload', async ({ page }) => {
    const tag = uid()
    await registerAndGetWorkspace(page)
    await createWorkspace(page, `ws-${tag}`)
    await createOrgMcpServer(page, `Override-${tag}`)

    await page.goto('/admin/mcp')
    const filterGroup = page.getByRole('group', { name: /filter/i })
    await filterGroup.getByRole('button', { name: 'Custom', exact: true }).click()
    await page.getByRole('button', { name: new RegExp(`Override-${tag}`, 'i') }).click()
    await expect(page.getByTestId('mcp-admin-detail-panel')).toBeVisible()

    await page.getByRole('tab', { name: 'Workspaces' }).click()

    const checkbox = page.getByTestId(`ws-override-checkbox-ws-${tag}`)
    await expect(checkbox).not.toBeChecked()

    await checkbox.check()
    await expect(checkbox).toBeChecked()

    await page.reload()
    await filterGroup.getByRole('button', { name: 'Custom', exact: true }).click()
    await page.getByRole('button', { name: new RegExp(`Override-${tag}`, 'i') }).click()
    await page.getByRole('tab', { name: 'Workspaces' }).click()

    await expect(page.getByTestId(`ws-override-checkbox-ws-${tag}`)).toBeChecked()
  })

  test('Add Custom shows form and creates a server', async ({ page }) => {
    const tag = uid()
    await registerAndGetWorkspace(page)

    await page.goto('/admin/mcp')
    await expect(page.getByRole('heading', { name: 'MCP Connectors' })).toBeVisible()

    await page.getByRole('button', { name: 'Add Custom' }).click()

    const form = page.getByTestId('mcp-admin-custom-form')
    await expect(form).toBeVisible()
    await expect(form.getByRole('heading', { name: 'Add custom MCP server' })).toBeVisible()

    await form.getByLabel('Name', { exact: true }).fill(`Custom-${tag}`)
    await form.getByLabel('Server URL').fill(`http://127.0.0.1:9/custom-${tag}`)
    // Default auth_method is "static" + credential_scope "org" → credential is required.
    await form.getByLabel('Credential', { exact: true }).fill('test-secret-value')
    await form.getByRole('button', { name: 'Create server' }).click()

    const detail = page.getByTestId('mcp-admin-detail-panel')
    await expect(detail).toBeVisible({ timeout: 10_000 })
    await expect(detail.getByRole('heading', { name: `Custom-${tag}` })).toBeVisible()

    // Sidebar reflects the new server.
    const sidebar = page.getByRole('complementary', { name: 'connector-list' })
    await expect(
      sidebar.getByRole('button', { name: new RegExp(`Custom-${tag}`, 'i') }),
    ).toBeVisible()
  })

  test('search filters connector list', async ({ page }) => {
    const tag = uid()
    await registerAndGetWorkspace(page)
    await createOrgMcpServer(page, `Alpha-${tag}`)
    await createOrgMcpServer(page, `Beta-${tag}`)

    await page.goto('/admin/mcp')
    await page.getByRole('searchbox', { name: /search/i }).fill(`Alpha-${tag}`)

    const sidebar = page.getByRole('complementary', { name: 'connector-list' })
    await expect(
      sidebar.getByRole('button', { name: new RegExp(`Alpha-${tag}`, 'i') }),
    ).toBeVisible()
    await expect(sidebar.getByRole('button', { name: new RegExp(`Beta-${tag}`, 'i') })).toHaveCount(
      0,
    )
  })
})
