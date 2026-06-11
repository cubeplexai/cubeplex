import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  listWorkspaces,
  createWorkspace,
  renameWorkspace,
  leaveWorkspace,
  type Workspace,
} from '../api/workspaces'

export interface WorkspaceStore {
  workspaces: Workspace[]
  isLoading: boolean
  error: string | null
  fetchList(client: ApiClient): Promise<void>
  create(client: ApiClient, name: string): Promise<Workspace>
  rename(client: ApiClient, wsId: string, name: string): Promise<void>
  leave(client: ApiClient, wsId: string): Promise<void>
  reset(): void
}

// One-user-one-org M1 assumption: a new workspace is created under the first
// workspace's org_id. When multi-org-per-user ships (P2), pass an explicit
// org id instead of reusing the first-seen one.
export const useWorkspaceStore = create<WorkspaceStore>((set, get) => ({
  workspaces: [],
  isLoading: false,
  error: null,

  async fetchList(client) {
    set({ isLoading: true, error: null })
    try {
      const workspaces = await listWorkspaces(client)
      set({ workspaces })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ isLoading: false })
    }
  },

  async create(client, name) {
    const existing = get().workspaces
    if (existing.length === 0) {
      throw new Error('Cannot create workspace: load workspaces first to determine org_id')
    }
    const orgId = existing[0].org_id
    const ws = await createWorkspace(client, { name, orgId })
    set((s) => ({ workspaces: [ws, ...s.workspaces] }))
    return ws
  },

  async rename(client, wsId, name) {
    const updated = await renameWorkspace(client, wsId, name)
    set((s) => ({
      workspaces: s.workspaces.map((w) => (w.id === wsId ? { ...w, name: updated.name } : w)),
    }))
  },

  async leave(client, wsId) {
    await leaveWorkspace(client, wsId)
    set((s) => ({ workspaces: s.workspaces.filter((w) => w.id !== wsId) }))
  },

  reset() {
    set({ workspaces: [], isLoading: false, error: null })
  },
}))
