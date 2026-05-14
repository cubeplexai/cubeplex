import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createApiClient } from '../../src/api/client'
import {
  wsGetToolCitations,
  wsPatchToolCitations,
  wsGetCatalogToolCitations,
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
