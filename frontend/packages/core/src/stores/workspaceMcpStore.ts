import { create } from 'zustand'

import type { ApiClient } from '../api/client'
import * as api from '../api/mcp'
import type {
  CredentialStatus,
  CredentialUpsertBody,
  MCPServer,
  MCPServerCreateWSBody,
  MCPServerListWS,
  MCPServerPatchBody,
  MCPTestConnectionBody,
  MCPTestConnectionResult,
  PromoteBody,
} from '../types/mcp'

export interface WorkspaceMcpStore {
  owned: MCPServer[]
  inherited: MCPServer[]
  loading: boolean
  error: string | null
  fetchAll(client: ApiClient, wsId: string): Promise<void>
  create(client: ApiClient, wsId: string, body: MCPServerCreateWSBody): Promise<MCPServer>
  update(client: ApiClient, wsId: string, id: string, body: MCPServerPatchBody): Promise<MCPServer>
  remove(client: ApiClient, wsId: string, id: string): Promise<void>
  refreshTools(client: ApiClient, wsId: string, id: string): Promise<MCPServer>
  testConnection(
    client: ApiClient,
    wsId: string,
    body: MCPTestConnectionBody,
  ): Promise<MCPTestConnectionResult>
  promote(client: ApiClient, wsId: string, id: string, body: PromoteBody): Promise<MCPServer>
  getMyCredentialStatus(client: ApiClient, wsId: string, id: string): Promise<CredentialStatus>
  setMyCredential(
    client: ApiClient,
    wsId: string,
    id: string,
    body: CredentialUpsertBody,
  ): Promise<CredentialStatus>
  clearMyCredential(client: ApiClient, wsId: string, id: string): Promise<void>
  getWorkspaceCredentialStatus(
    client: ApiClient,
    wsId: string,
    id: string,
  ): Promise<CredentialStatus>
  setWorkspaceCredential(
    client: ApiClient,
    wsId: string,
    id: string,
    body: CredentialUpsertBody,
  ): Promise<CredentialStatus>
  clearWorkspaceCredential(client: ApiClient, wsId: string, id: string): Promise<void>
  reset(): void
}

export const useWorkspaceMcpStore = create<WorkspaceMcpStore>((set, get) => ({
  owned: [],
  inherited: [],
  loading: false,
  error: null,

  async fetchAll(client, wsId) {
    set({ loading: true, error: null })
    try {
      const list: MCPServerListWS = await api.wsListServers(client, wsId)
      set({ owned: list.owned, inherited: list.inherited })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ loading: false })
    }
  },

  async create(client, wsId, body) {
    const created = await api.wsCreateServer(client, wsId, body)
    set({ owned: [...get().owned, created] })
    return created
  },

  async update(client, wsId, id, body) {
    const updated = await api.wsPatchServer(client, wsId, id, body)
    set({ owned: get().owned.map((server) => (server.id === id ? updated : server)) })
    return updated
  },

  async remove(client, wsId, id) {
    await api.wsDeleteServer(client, wsId, id)
    set({ owned: get().owned.filter((server) => server.id !== id) })
  },

  async refreshTools(client, wsId, id) {
    const refreshed = await api.wsRefreshTools(client, wsId, id)
    set({ owned: get().owned.map((server) => (server.id === id ? refreshed : server)) })
    return refreshed
  },

  testConnection(client, wsId, body) {
    return api.wsTestConnection(client, wsId, body)
  },

  async promote(client, wsId, id, body) {
    const promoted = await api.wsPromote(client, wsId, id, body)
    set({
      owned: get().owned.filter((server) => server.id !== id),
      inherited: [...get().inherited.filter((server) => server.id !== id), promoted],
    })
    return promoted
  },

  getMyCredentialStatus(client, wsId, id) {
    return api.wsGetMyCredential(client, wsId, id)
  },

  setMyCredential(client, wsId, id, body) {
    return api.wsPutMyCredential(client, wsId, id, body)
  },

  clearMyCredential(client, wsId, id) {
    return api.wsDeleteMyCredential(client, wsId, id)
  },

  getWorkspaceCredentialStatus(client, wsId, id) {
    return api.wsGetWorkspaceCredential(client, wsId, id)
  },

  setWorkspaceCredential(client, wsId, id, body) {
    return api.wsPutWorkspaceCredential(client, wsId, id, body)
  },

  clearWorkspaceCredential(client, wsId, id) {
    return api.wsDeleteWorkspaceCredential(client, wsId, id)
  },

  reset() {
    set({ owned: [], inherited: [], loading: false, error: null })
  },
}))
