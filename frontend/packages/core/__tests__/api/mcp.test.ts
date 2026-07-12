import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createApiClient } from '../../src/api/client'
import {
  adminCreateTemplate,
  adminDistribute,
  adminListCatalog,
  wsCreateMyGrant,
  wsListCatalog,
  wsListEffectiveConnectors,
  wsSetTemplateState,
} from '../../src/api/mcp'

describe('MCP catalog API', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('adminListCatalog GETs the admin catalog endpoint', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }))
    const client = createApiClient('')
    await adminListCatalog(client)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/admin/mcp/catalog')
  })

  it('wsListCatalog GETs the workspace catalog endpoint', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }))
    const client = createApiClient('')
    await wsListCatalog(client, 'ws-x')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/catalog')
  })

  it('adminCreateTemplate POSTs to admin templates endpoint', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ template_id: 'mcptpl-1' }), { status: 201 }),
    )
    const client = createApiClient('')
    const body = {
      name: 'My Server',
      server_url: 'https://example.com/mcp',
      transport: 'streamable_http' as const,
      auth_method: 'static' as const,
    }
    await adminCreateTemplate(client, body)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/admin/mcp/templates')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('adminDistribute POSTs to the distribute endpoint', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ template: { template_id: 'mcptpl-1' } }), { status: 200 }),
    )
    const client = createApiClient('')
    await adminDistribute(client, 'mcptpl-1', { enable_existing: true, auto_enroll: false })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/admin/mcp/templates/mcptpl-1/distribute')
  })

  it('wsSetTemplateState PUTs to the ws state endpoint', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ template: {}, enabled: true }), { status: 200 }),
    )
    const client = createApiClient('')
    await wsSetTemplateState(client, 'ws-x', 'mcptpl-1', { enabled: true })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/ws/ws-x/mcp/templates/mcptpl-1/state')
    expect((init as RequestInit).method).toBe('PUT')
  })

  it('wsListEffectiveConnectors GETs the workspace connectors endpoint', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }))
    const client = createApiClient('')
    await wsListEffectiveConnectors(client, 'ws-x')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/connectors')
  })

  it('wsCreateMyGrant posts to the per-user grant endpoint', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ connector_id: 'mcpco-1' }), { status: 201 }),
    )
    const client = createApiClient('')
    await wsCreateMyGrant(client, 'ws-x', 'mcins-1', { credential_plaintext: 'tok' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/installs/mcins-1/grants/me')
  })
})
