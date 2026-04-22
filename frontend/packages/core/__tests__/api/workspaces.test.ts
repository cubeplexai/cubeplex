import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createApiClient } from '../../src/api/client'
import { listWorkspaces, createWorkspace } from '../../src/api/workspaces'

describe('workspaces API', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('listWorkspaces returns array of workspaces', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify([
          { id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' },
          { id: 'w2', name: 'Team', org_id: 'o1', role: 'member' },
        ]),
        { status: 200 },
      ),
    )
    const client = createApiClient('')
    const list = await listWorkspaces(client)
    expect(list).toHaveLength(2)
    expect(list[0]).toMatchObject({ id: 'w1', name: 'Personal', role: 'admin' })
  })

  it('createWorkspace POSTs { name, org_id } and returns the new ws', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'w3', name: 'Ops', org_id: 'o1' }), { status: 201 }),
    )
    const client = createApiClient('')
    const ws = await createWorkspace(client, { name: 'Ops', orgId: 'o1' })
    expect(ws).toMatchObject({ id: 'w3', name: 'Ops', org_id: 'o1' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/workspaces')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ name: 'Ops', org_id: 'o1' })
  })
})
