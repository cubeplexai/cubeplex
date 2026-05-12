import { expect, type Page } from '@playwright/test'

import { registerAsAdmin } from '../skills/_helpers'

export async function registerAndGetWorkspace(page: Page): Promise<{ wsId: string }> {
  await registerAsAdmin(page)
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  const match = page.url().match(/\/w\/([^/?#]+)/)
  if (!match) throw new Error(`Could not parse workspace id from URL: ${page.url()}`)
  return { wsId: match[1] }
}

async function csrf(page: Page): Promise<string> {
  const cookies = await page.context().cookies()
  const name = process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME ?? 'cubebox_csrf'
  return cookies.find((cookie) => cookie.name === name)?.value ?? ''
}

export async function createWorkspace(page: Page, name: string): Promise<{ id: string }> {
  const listResp = await page.request.get('/api/v1/workspaces')
  if (!listResp.ok()) throw new Error(`list workspaces failed: ${listResp.status()}`)
  const workspaces = (await listResp.json()) as Array<{ id: string; org_id: string }>
  const orgId = workspaces[0]?.org_id
  if (!orgId) throw new Error('No bootstrap workspace/org found')

  const createResp = await page.request.post('/api/v1/workspaces', {
    headers: { 'X-CSRF-Token': await csrf(page) },
    data: { name, org_id: orgId },
  })
  if (!createResp.ok()) throw new Error(`create workspace failed: ${createResp.status()}`)
  return (await createResp.json()) as { id: string }
}

export async function createOrgMcpServer(page: Page, name: string): Promise<{ id: string }> {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-')
  const resp = await page.request.post('/api/v1/admin/mcp/servers', {
    headers: { 'X-CSRF-Token': await csrf(page) },
    data: {
      name,
      server_url: `http://127.0.0.1:9/${slug}`,
      transport: 'streamable_http',
      auth_method: 'static',
      credential_scope: 'org',
      credential_name: `${name} token`,
      credential_plaintext: 'test-secret-value',
      headers: {},
      timeout: 1,
      sse_read_timeout: 1,
    },
  })
  if (!resp.ok()) throw new Error(`create MCP server failed: ${resp.status()}`)
  return (await resp.json()) as { id: string }
}
