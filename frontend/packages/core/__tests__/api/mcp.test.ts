import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createApiClient } from '../../src/api/client'
import {
  adminCreateInstall,
  adminListTemplates,
  wsCreateInstall,
  wsCreateMyGrant,
  wsListEffectiveConnectors,
  wsListTemplates,
  wsPatchConnectorState,
} from '../../src/api/mcp'

describe('MCP four-layer API', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('uses template and install paths for workspace MCP', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }))
    const client = createApiClient('')
    await wsListTemplates(client, 'ws-x')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/templates')
  })

  it('adminListTemplates hits the admin scope', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }))
    const client = createApiClient('')
    await adminListTemplates(client)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/admin/mcp/templates')
  })

  it('wsCreateInstall POSTs to workspace install endpoint', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ install_id: 'mcins-1', connector_id: 'mcpco-1' }), {
        status: 201,
      }),
    )
    const client = createApiClient('')
    await wsCreateInstall(client, 'ws-x', { template_id: 'mctpl-1' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/ws/ws-x/mcp/installs')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('adminCreateInstall POSTs to admin install endpoint', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ install_id: 'mcins-1', connector_id: 'mcpco-1' }), {
        status: 201,
      }),
    )
    const client = createApiClient('')
    const install = await adminCreateInstall(client, { template_id: 'mctpl-1' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/admin/mcp/installs')
    expect(install.connector_id).toBe('mcpco-1')
  })

  it('wsListEffectiveConnectors GETs the workspace connectors endpoint', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }))
    const client = createApiClient('')
    await wsListEffectiveConnectors(client, 'ws-x')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/connectors')
  })

  it('wsPatchConnectorState PATCHes the connector state', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 }))
    const client = createApiClient('')
    await wsPatchConnectorState(client, 'ws-x', 'mcins-1', { enabled: false })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/ws/ws-x/mcp/connectors/mcins-1/state')
    expect((init as RequestInit).method).toBe('PATCH')
  })

  it('wsCreateMyGrant posts to the per-user grant endpoint', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ install_id: 'mcins-1', connector_id: 'mcpco-1' }), {
        status: 201,
      }),
    )
    const client = createApiClient('')
    await wsCreateMyGrant(client, 'ws-x', 'mcins-1', { credential_plaintext: 'tok' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/installs/mcins-1/grants/me')
  })
})
