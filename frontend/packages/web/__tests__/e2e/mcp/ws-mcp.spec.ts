import { expect, test } from '@playwright/test'

import { registerAndGetWorkspace } from './_helpers'

test.describe('Workspace MCP', () => {
  test('member creates workspace-shared MCP', async ({ page }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    await page.goto(`/w/${wsId}/integrations/mcp/new`)
    await expect(page.getByRole('heading', { name: 'Add MCP server' })).toBeVisible()

    await page.getByLabel('Name *').fill('MyTool')
    await page.getByLabel('Server URL *').pressSequentially('http://127.0.0.1:9/mcp')
    await page.getByText('Workspace shared').click()
    await page.getByLabel('API key / token').fill('tok')
    await expect(page.getByRole('button', { name: 'Test connection' })).toBeEnabled()
    await page.getByRole('button', { name: 'Test connection' }).click()

    await expect(page.getByText('Connection failed').first()).toBeVisible()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page).toHaveURL(/\/integrations\/mcp\/[^/]+$/, { timeout: 10_000 })
  })

  test('promote dialog shows share-credential options for workspace scope', async ({ page }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    await page.goto(`/w/${wsId}/integrations/mcp/new`)
    await expect(page.getByRole('heading', { name: 'Add MCP server' })).toBeVisible()

    await page.getByLabel('Name *').fill('PromoteTool')
    await page.getByLabel('Server URL *').pressSequentially('http://127.0.0.1:9/mcp')
    await page.getByText('Workspace shared').click()
    await page.getByLabel('API key / token').fill('tok')
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page).toHaveURL(/\/integrations\/mcp\/[^/]+$/, { timeout: 10_000 })

    await page.getByRole('button', { name: 'Share to org' }).click()

    await expect(page.getByRole('heading', { name: 'Promote MCP server' })).toBeVisible()
    await expect(page.getByText('Share credential with organization')).toBeVisible()
    await expect(page.getByText('Promote without credential')).toBeVisible()
  })
})
