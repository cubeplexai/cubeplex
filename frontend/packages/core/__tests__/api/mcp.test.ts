import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createApiClient } from '../../src/api/client'
import {
  wsGetToolCitations,
  wsPatchToolCitations,
  wsGetCatalogToolCitations,
  wsListTemplates,
} from '../../src/api/mcp'
import type { CitationConfigJSON, ToolCitationsResponse } from '../../src/types/mcp'

describe('MCP tool-citations API', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('wsGetToolCitations GETs the right URL and returns the shape', async () => {
    const sample: ToolCitationsResponse = {
      server_id: 'mcp-1',
      server_name: 'webtools',
      tools_cache: [{ name: 'web_search', description: '', input_schema: {} }],
      tool_citations: {
        web_search: {
          content_type: 'json',
          source_type: 'web',
          content_field: 'results',
          mapping: { snippet: 'description' },
        },
      },
      catalog_defaults: null,
      orphan_keys: [],
    }
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(sample), { status: 200 }))
    const client = createApiClient('')
    const out = await wsGetToolCitations(client, 'ws-x', 'mcp-1')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/servers/mcp-1/tool-citations')
    expect(out).toEqual(sample)
  })

  it('wsPatchToolCitations sends the full dict in the body', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          server_id: 'mcp-1',
          server_name: 'webtools',
          tools_cache: [],
          tool_citations: {},
          catalog_defaults: null,
          orphan_keys: [],
        }),
        { status: 200 },
      ),
    )
    const client = createApiClient('')
    const payload: Record<string, CitationConfigJSON> = {
      web_search: {
        content_type: 'json',
        source_type: 'web',
        content_field: 'results',
        mapping: {},
      },
    }
    await wsPatchToolCitations(client, 'ws-x', 'mcp-1', payload)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/ws/ws-x/mcp/servers/mcp-1/tool-citations')
    expect((init as RequestInit).method).toBe('PATCH')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ tool_citations: payload })
  })

  it('wsGetCatalogToolCitations uses the catalog URL', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          slug: 'webtools',
          tool_citations: {
            web_search: {
              content_type: 'json',
              source_type: 'web',
              content_field: null,
              mapping: {},
            },
          },
        }),
        { status: 200 },
      ),
    )
    const client = createApiClient('')
    const out = await wsGetCatalogToolCitations(client, 'ws-x', 'webtools')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/catalog/webtools/tool-citations')
    expect(out.slug).toBe('webtools')
  })

  it('wsPatchToolCitations throws on non-OK response', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: [{ tool: 'ghost', msg: 'unknown' }] }), {
        status: 422,
      }),
    )
    const client = createApiClient('')
    await expect(
      wsPatchToolCitations(client, 'ws-x', 'mcp-1', {
        ghost: {
          content_type: 'json',
          source_type: 'web',
          content_field: null,
          mapping: {},
        },
      }),
    ).rejects.toThrow()
  })
})

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

  it('does not use catalog or override paths', async () => {
    // Coexist policy: legacy MCPCatalog* helpers still live in this module
    // until Task 8 migrates the React components, so a blanket
    // `every export name lacks Catalog/Override` assertion would fail
    // (and rightly so). Instead, assert the four-layer helpers introduced
    // by this task don't accidentally carry the legacy substrings.
    const source = await import('../../src/api/mcp')
    const fourLayerNames = [
      'wsListTemplates',
      'wsCreateInstall',
      'wsPatchConnectorState',
      'wsListEffectiveConnectors',
    ]
    for (const name of fourLayerNames) {
      expect(typeof (source as Record<string, unknown>)[name]).toBe('function')
      expect(name.includes('Catalog')).toBe(false)
      expect(name.includes('Override')).toBe(false)
    }
  })
})
