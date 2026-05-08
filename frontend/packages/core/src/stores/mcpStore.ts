import { create } from 'zustand'

import { ApiError, type ApiClient } from '../api/client'
import * as api from '../api/mcp'
import type {
  MCPCatalogConnector,
  MCPCatalogInstallRequest,
  MCPCatalogInstallResult,
  MCPInstallSwitchAuthRequest,
  MCPOAuthStartResult,
  MCPOverrideUpdateBody,
  MCPServer,
  MCPServerCreateAdminBody,
  MCPServerPatchBody,
  MCPTestConnectionBody,
  MCPTestConnectionResult,
  WorkspaceOverride,
} from '../types/mcp'

export interface CatalogErrorEnvelope {
  code: string
  message: string
}

function toCatalogError(err: unknown): CatalogErrorEnvelope {
  if (err instanceof ApiError) {
    return { code: err.code ?? 'unknown', message: err.message }
  }
  return { code: 'unknown', message: (err as Error).message ?? 'Unknown error' }
}

interface CatalogListParams {
  q?: string
  provider?: string
}

export interface McpStore {
  servers: MCPServer[]
  loading: boolean
  error: string | null
  // catalog state
  catalog: MCPCatalogConnector[]
  catalogLoading: boolean
  catalogError: CatalogErrorEnvelope | null
  pendingOAuthInstallId: string | null
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
  // catalog actions (admin)
  fetchCatalog(client: ApiClient, wsId: string, params?: CatalogListParams): Promise<void>
  installFromCatalog(
    client: ApiClient,
    wsId: string,
    catalogId: string,
    body: MCPCatalogInstallRequest,
  ): Promise<MCPCatalogInstallResult>
  patchInstall(
    client: ApiClient,
    wsId: string,
    installId: string,
    body: MCPInstallSwitchAuthRequest,
  ): Promise<MCPCatalogInstallResult>
  deleteInstall(client: ApiClient, wsId: string, installId: string): Promise<void>
  startOAuth(client: ApiClient, installId: string): Promise<MCPOAuthStartResult>
  clearPendingOAuth(): void
  reset(): void
}

export const useMcpStore = create<McpStore>((set, get) => ({
  servers: [],
  loading: false,
  error: null,
  catalog: [],
  catalogLoading: false,
  catalogError: null,
  pendingOAuthInstallId: null,

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

  async fetchCatalog(client, wsId, params) {
    set({ catalogLoading: true, catalogError: null })
    try {
      const items = await api.wsCatalogList(client, wsId, params)
      set({ catalog: items })
    } catch (err) {
      set({ catalogError: toCatalogError(err) })
    } finally {
      set({ catalogLoading: false })
    }
  },

  async installFromCatalog(client, wsId, catalogId, body) {
    set({ catalogError: null })
    try {
      const result = await api.adminCatalogInstall(client, catalogId, body)
      if (result.requires_oauth) {
        set({ pendingOAuthInstallId: result.install_id })
      }
      // refetch so status fields update
      await get().fetchCatalog(client, wsId)
      return result
    } catch (err) {
      const env = toCatalogError(err)
      set({ catalogError: env })
      throw err
    }
  },

  async patchInstall(client, wsId, installId, body) {
    set({ catalogError: null })
    try {
      const result = await api.adminCatalogPatchInstall(client, installId, body)
      if (result.requires_oauth) {
        set({ pendingOAuthInstallId: result.install_id })
      }
      await get().fetchCatalog(client, wsId)
      return result
    } catch (err) {
      const env = toCatalogError(err)
      set({ catalogError: env })
      throw err
    }
  },

  async deleteInstall(client, wsId, installId) {
    set({ catalogError: null })
    try {
      await api.adminCatalogDeleteInstall(client, installId)
      await get().fetchCatalog(client, wsId)
    } catch (err) {
      const env = toCatalogError(err)
      set({ catalogError: env })
      throw err
    }
  },

  async startOAuth(client, installId) {
    set({ catalogError: null })
    try {
      const result = await api.adminOAuthStart(client, installId)
      set({ pendingOAuthInstallId: installId })
      return result
    } catch (err) {
      const env = toCatalogError(err)
      set({ catalogError: env })
      throw err
    }
  },

  clearPendingOAuth() {
    set({ pendingOAuthInstallId: null })
  },

  reset() {
    set({
      servers: [],
      loading: false,
      error: null,
      catalog: [],
      catalogLoading: false,
      catalogError: null,
      pendingOAuthInstallId: null,
    })
  },
}))
