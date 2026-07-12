import { describe, it, expect, vi } from 'vitest'

import { adminListCatalog, adminPurgeTemplate, type ApiClient } from '../../src'

describe('adminListCatalog', () => {
  it('GETs /api/v1/admin/mcp/catalog', async () => {
    const client = {
      get: vi.fn(async () => ({
        ok: true,
        json: async () => ({ items: [] }),
      })),
    } as unknown as ApiClient
    const res = await adminListCatalog(client)
    expect(res.items).toEqual([])
    expect(client.get).toHaveBeenCalledWith('/api/v1/admin/mcp/catalog')
  })
})

describe('adminPurgeTemplate', () => {
  it('POSTs to /api/v1/admin/mcp/templates/{id}/purge', async () => {
    const client = {
      post: vi.fn(async () => ({ ok: true })),
    } as unknown as ApiClient
    await adminPurgeTemplate(client, 'mcptpl-1')
    expect(client.post).toHaveBeenCalledWith('/api/v1/admin/mcp/templates/mcptpl-1/purge', {})
  })
})
