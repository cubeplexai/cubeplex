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
    expect(created.role).toBe('admin')
    expect(created.sandbox_mode).toBe('per_user')
    const list = useWorkspaceStore.getState().workspaces
    expect(list[0].id).toBe('w2')
    expect(list[0].role).toBe('admin')
    expect(list[0].sandbox_mode).toBe('per_user')
    expect(list).toHaveLength(2)
  })

  it('create forwards sandboxMode and preserves workspace mode on prepended entry', async () => {
    useWorkspaceStore.setState({
      workspaces: [{ id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' }],
    })
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ id: 'w3', name: 'Shared Team', org_id: 'o1', sandbox_mode: 'workspace' }),
        { status: 201 },
      ),
    )
    const client = createApiClient('')
    const created = await useWorkspaceStore.getState().create(client, 'Shared Team', 'workspace')
    expect(created).toMatchObject({
      id: 'w3',
      name: 'Shared Team',
      role: 'admin',
      sandbox_mode: 'workspace',
    })
    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      name: 'Shared Team',
      org_id: 'o1',
      sandbox_mode: 'workspace',
    })
    expect(useWorkspaceStore.getState().workspaces[0]).toMatchObject({
      id: 'w3',
      role: 'admin',
      sandbox_mode: 'workspace',
    })
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

  it('retains a workspace until hard-delete cleanup completes', async () => {
    useWorkspaceStore.setState({
      workspaces: [{ id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' }],
    })
    fetchMock
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ deleted: false, cleanup_pending: true }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ deleted: true, cleanup_pending: false }), { status: 200 }),
      )
    const client = createApiClient('')

    const pending = await useWorkspaceStore.getState().deleteWs(client, 'w1')
    expect(pending.cleanup_pending).toBe(true)
    expect(useWorkspaceStore.getState().workspaces).toHaveLength(1)

    const deleted = await useWorkspaceStore.getState().deleteWs(client, 'w1')
    expect(deleted.deleted).toBe(true)
    expect(useWorkspaceStore.getState().workspaces).toEqual([])
  })
})
