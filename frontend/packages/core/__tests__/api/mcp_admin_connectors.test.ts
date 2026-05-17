import { describe, it, expect, vi } from 'vitest'

import { adminListConnectors, wsListAvailable, type ApiClient } from '../../src'

describe('adminListConnectors', () => {
  it('GETs /api/v1/admin/mcp/connectors', async () => {
    const client = {
      get: vi.fn(async () => ({
        ok: true,
        json: async () => ({ items: [] }),
      })),
    } as unknown as ApiClient
    const res = await adminListConnectors(client)
    expect(res.items).toEqual([])
    expect(client.get).toHaveBeenCalledWith('/api/v1/admin/mcp/connectors')
  })
})

describe('wsListAvailable', () => {
  it('GETs /api/v1/ws/{ws}/mcp/available', async () => {
    const client = {
      get: vi.fn(async () => ({
        ok: true,
        json: async () => ({ items: [] }),
      })),
    } as unknown as ApiClient
    const res = await wsListAvailable(client, 'ws-1')
    expect(res.items).toEqual([])
    expect(client.get).toHaveBeenCalledWith('/api/v1/ws/ws-1/mcp/available')
  })
})
