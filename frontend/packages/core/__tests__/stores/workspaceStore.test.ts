import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useWorkspaceStore } from '../../src/stores/workspaceStore'
import { createApiClient } from '../../src/api/client'

describe('workspaceStore', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    useWorkspaceStore.setState({ workspaces: [], lastOrgId: null, isLoading: false, error: null })
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('fetchList populates workspaces', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify([{ id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' }]), {
        status: 200,
      }),
    )
    const client = createApiClient('')
    await useWorkspaceStore.getState().fetchList(client)
    expect(useWorkspaceStore.getState().workspaces).toHaveLength(1)
  })

  it('create prepends new workspace to list', async () => {
    useWorkspaceStore.setState({
      workspaces: [{ id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' }],
    })
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'w2', name: 'Team', org_id: 'o1' }), { status: 201 }),
    )
    const client = createApiClient('')
    const created = await useWorkspaceStore.getState().create(client, 'Team')
    expect(created.id).toBe('w2')
    const list = useWorkspaceStore.getState().workspaces
    expect(list[0].id).toBe('w2')
    expect(list).toHaveLength(2)
  })

  it('create throws when no workspaces (no org_id to use)', async () => {
    const client = createApiClient('')
    await expect(useWorkspaceStore.getState().create(client, 'Team')).rejects.toThrow(
      /load workspaces first/i,
    )
  })

  it('reset clears list', () => {
    useWorkspaceStore.setState({
      workspaces: [{ id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' }],
    })
    useWorkspaceStore.getState().reset()
    expect(useWorkspaceStore.getState().workspaces).toEqual([])
  })
})
