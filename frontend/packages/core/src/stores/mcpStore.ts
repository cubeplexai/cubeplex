import { create } from 'zustand'

import type { ApiClient } from '../api/client'
import * as api from '../api/mcp'
import type {
  MCPOverrideUpdateBody,
  MCPServer,
  MCPServerCreateAdminBody,
  MCPServerPatchBody,
  MCPTestConnectionBody,
  MCPTestConnectionResult,
  WorkspaceOverride,
} from '../types/mcp'

export interface McpStore {
  servers: MCPServer[]
  loading: boolean
  error: string | null
  fetchAll(client: ApiClient): Promise<void>
  create(client: ApiClient, body: MCPServerCreateAdminBody): Promise<MCPServer>
  update(client: ApiClient, id: string, body: MCPServerPatchBody): Promise<MCPServer>
  remove(client: ApiClient, id: string): Promise<void>
  refreshTools(client: ApiClient, id: string): Promise<MCPServer>
  testConnection(client: ApiClient, body: MCPTestConnectionBody): Promise<MCPTestConnectionResult>
  fetchOverrides(client: ApiClient, id: string): Promise<WorkspaceOverride[]>
  saveOverride(
    client: ApiClient,
    id: string,
    body: MCPOverrideUpdateBody,
  ): Promise<WorkspaceOverride[]>
  reset(): void
}

export const useMcpStore = create<McpStore>((set, get) => ({
  servers: [],
  loading: false,
  error: null,

  async fetchAll(client) {
    set({ loading: true, error: null })
    try {
      const servers = await api.adminListServers(client)
      set({ servers })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ loading: false })
    }
  },

  async create(client, body) {
    const created = await api.adminCreateServer(client, body)
    set({ servers: [...get().servers, created] })
    return created
  },

  async update(client, id, body) {
    const updated = await api.adminPatchServer(client, id, body)
    set({ servers: get().servers.map((server) => (server.id === id ? updated : server)) })
    return updated
  },

  async remove(client, id) {
    await api.adminDeleteServer(client, id)
    set({ servers: get().servers.filter((server) => server.id !== id) })
  },

  async refreshTools(client, id) {
    const refreshed = await api.adminRefreshTools(client, id)
    set({ servers: get().servers.map((server) => (server.id === id ? refreshed : server)) })
    return refreshed
  },

  testConnection(client, body) {
    return api.adminTestConnection(client, body)
  },

  fetchOverrides(client, id) {
    return api.adminGetOverrides(client, id)
  },

  saveOverride(client, id, body) {
    return api.adminPutOverride(client, id, body)
  },

  reset() {
    set({ servers: [], loading: false, error: null })
  },
}))
