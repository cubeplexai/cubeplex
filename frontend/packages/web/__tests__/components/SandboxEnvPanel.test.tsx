import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { EnvEntryOut, Workspace } from '@cubeplex/core'

const mocks = vi.hoisted(() => ({
  listWsEnvWorkspace: vi.fn(),
  listWsEnvMe: vi.fn(),
  createWsEnvWorkspace: vi.fn(),
  createWsEnvMe: vi.fn(),
  updateWsEnvWorkspace: vi.fn(),
  updateWsEnvMe: vi.fn(),
  deleteWsEnvWorkspace: vi.fn(),
  deleteWsEnvMe: vi.fn(),
  client: { baseUrl: '' },
}))

vi.mock('@cubeplex/core', async () => {
  const { create } = await import('zustand')
  const useWorkspaceStore = create(() => ({
    workspaces: [] as Workspace[],
    error: null as string | null,
  }))
  return {
    createApiClient: () => mocks.client,
    listWsEnvWorkspace: mocks.listWsEnvWorkspace,
    listWsEnvMe: mocks.listWsEnvMe,
    createWsEnvWorkspace: mocks.createWsEnvWorkspace,
    createWsEnvMe: mocks.createWsEnvMe,
    updateWsEnvWorkspace: mocks.updateWsEnvWorkspace,
    updateWsEnvMe: mocks.updateWsEnvMe,
    deleteWsEnvWorkspace: mocks.deleteWsEnvWorkspace,
    deleteWsEnvMe: mocks.deleteWsEnvMe,
    useWorkspaceStore,
  }
})

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

import { useWorkspaceStore } from '@cubeplex/core'
import { SandboxEnvPanel } from '@/components/workspace-settings/SandboxEnvPanel'

const sampleWsEntry: EnvEntryOut = {
  id: 'env-1',
  env_name: 'SHARED_API_KEY',
  scope: 'workspace',
  is_secret: true,
  hosts: ['api.example.com'],
  header_names: null,
  warnings: [],
  created_at: '2026-11-16T00:00:00Z',
  updated_at: '2026-11-16T00:00:00Z',
}

describe('SandboxEnvPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useWorkspaceStore.setState({ workspaces: [], error: null })
  })

  it('shows read-only workspace env vars and skips personal env fetch for non-admin members in workspace-shared mode', async () => {
    useWorkspaceStore.setState({
      workspaces: [
        {
          id: 'ws-shared',
          name: 'Shared Team',
          org_id: 'org-1',
          role: 'member',
          sandbox_mode: 'workspace',
        },
      ],
    })
    mocks.listWsEnvWorkspace.mockResolvedValue({ entries: [sampleWsEntry] })

    render(<SandboxEnvPanel wsId="ws-shared" />)

    await waitFor(() => expect(screen.getByText('SHARED_API_KEY')).toBeInTheDocument())
    expect(mocks.listWsEnvWorkspace).toHaveBeenCalledWith(mocks.client, 'ws-shared')
    expect(mocks.listWsEnvMe).not.toHaveBeenCalled()

    expect(screen.getByText('sharedWorkspaceMemberDescription')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'addPersonalVar' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'addWorkspaceVar' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'actionEdit' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'actionDelete' })).not.toBeInTheDocument()
  })

  it('allows workspace admins in workspace-shared mode to manage workspace vars while hiding personal vars', async () => {
    useWorkspaceStore.setState({
      workspaces: [
        {
          id: 'ws-shared',
          name: 'Shared Team',
          org_id: 'org-1',
          role: 'admin',
          sandbox_mode: 'workspace',
        },
      ],
    })
    mocks.listWsEnvWorkspace.mockResolvedValue({ entries: [sampleWsEntry] })

    render(<SandboxEnvPanel wsId="ws-shared" />)

    await waitFor(() => expect(screen.getByText('SHARED_API_KEY')).toBeInTheDocument())
    expect(mocks.listWsEnvWorkspace).toHaveBeenCalledWith(mocks.client, 'ws-shared')
    expect(mocks.listWsEnvMe).not.toHaveBeenCalled()

    expect(screen.queryByRole('button', { name: 'addPersonalVar' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'actionEdit' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'actionDelete' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'addWorkspaceVar' }))
    expect(screen.queryByText('scopePersonal')).not.toBeInTheDocument()
  })

  it('keeps personal env behavior in per_user workspaces for regular members', async () => {
    useWorkspaceStore.setState({
      workspaces: [
        {
          id: 'ws-per-user',
          name: 'Personal Team',
          org_id: 'org-1',
          role: 'member',
          sandbox_mode: 'per_user',
        },
      ],
    })
    mocks.listWsEnvMe.mockResolvedValue({ entries: [] })

    render(<SandboxEnvPanel wsId="ws-per-user" />)

    await waitFor(() => expect(mocks.listWsEnvMe).toHaveBeenCalledWith(mocks.client, 'ws-per-user'))
    expect(mocks.listWsEnvWorkspace).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'addPersonalVar' })).toBeInTheDocument()
  })

  it('waits for workspace list hydration before fetching or showing personal env controls', async () => {
    mocks.listWsEnvWorkspace.mockResolvedValue({ entries: [sampleWsEntry] })

    render(<SandboxEnvPanel wsId="ws-shared" />)

    // Before workspaceStore.fetchList resolves, neither endpoint is called and addPersonalVar does not flash
    expect(mocks.listWsEnvMe).not.toHaveBeenCalled()
    expect(mocks.listWsEnvWorkspace).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'addPersonalVar' })).not.toBeInTheDocument()

    act(() => {
      useWorkspaceStore.setState({
        workspaces: [
          {
            id: 'ws-shared',
            name: 'Shared Team',
            org_id: 'org-1',
            role: 'member',
            sandbox_mode: 'workspace',
          },
        ],
      })
    })

    await waitFor(() => expect(screen.getByText('SHARED_API_KEY')).toBeInTheDocument())
    expect(mocks.listWsEnvWorkspace).toHaveBeenCalledTimes(1)
    expect(mocks.listWsEnvMe).not.toHaveBeenCalled()
  })

  it('surfaces workspaceStore error instead of spinning indefinitely if workspace list fails', () => {
    useWorkspaceStore.setState({ workspaces: [], error: 'Workspace list failed' })

    render(<SandboxEnvPanel wsId="ws-shared" />)

    expect(screen.getByText('loadError')).toBeInTheDocument()
    expect(mocks.listWsEnvMe).not.toHaveBeenCalled()
    expect(mocks.listWsEnvWorkspace).not.toHaveBeenCalled()
  })
})
