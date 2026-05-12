import { create } from 'zustand'

import { type ApiClient } from '../api/client'
import * as api from '../api/mcp'
import type {
  MCPAdminConnector,
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
import { type CatalogErrorEnvelope, toCatalogError } from './mcpShared'

function mergeConnectors(
  catalog: MCPCatalogConnector[],
  servers: MCPServer[],
  overrideCounts: Map<string, number>,
): MCPAdminConnector[] {
  const result: MCPAdminConnector[] = []
  const serverByCatalogId = new Map<string, MCPServer>()

  for (const srv of servers) {
    if (srv.owner_workspace_id !== null) continue
    const matched = catalog.find((c) => c.org_install_id === srv.id)
    if (matched) {
      serverByCatalogId.set(matched.id, srv)
      continue
    }
    result.push({
      kind: 'custom',
      id: srv.id,
      name: srv.name,
      provider: '',
      description: '',
      server_url: srv.server_url,
      transport: srv.transport,
      installed: true,
      server: srv,
      authed: srv.authed,
      tool_count: srv.tools_cache?.length ?? 0,
      workspace_count: overrideCounts.get(srv.id) ?? 0,
      last_error: srv.last_error,
    })
  }

  for (const cat of catalog) {
    const srv =
      serverByCatalogId.get(cat.id) ??
      (cat.org_install_id ? servers.find((s) => s.id === cat.org_install_id) : undefined)
    result.push({
      kind: 'catalog',
      id: cat.org_install_id ?? cat.id,
      name: cat.name,
      provider: cat.provider,
      description: cat.description,
      server_url: cat.server_url,
      transport: cat.transport,
      catalog_id: cat.id,
      supported_auth_methods: cat.supported_auth_methods,
      static_form_fields: cat.static_form_fields,
      installed: cat.org_install_id !== null,
      server: srv ?? undefined,
      authed: srv?.authed ?? false,
      tool_count: srv?.tools_cache?.length ?? 0,
      workspace_count: overrideCounts.get(srv?.id ?? '') ?? 0,
      last_error: srv?.last_error ?? null,
    })
  }

  return result
}

export interface McpStore {
  connectors: MCPAdminConnector[]
  loading: boolean
  error: CatalogErrorEnvelope | null
  selectedId: string | null
  pendingOAuthInstallId: string | null
  overrideCounts: Map<string, number>

  fetchAll(client: ApiClient, wsId: string): Promise<void>
  setSelectedId(id: string | null): void

  createCustom(client: ApiClient, body: MCPServerCreateAdminBody): Promise<MCPServer>
  updateServer(client: ApiClient, id: string, body: MCPServerPatchBody): Promise<MCPServer>
  deleteServer(client: ApiClient, id: string, wsId: string): Promise<void>
  refreshTools(client: ApiClient, id: string): Promise<MCPServer>
  testConnection(client: ApiClient, body: MCPTestConnectionBody): Promise<MCPTestConnectionResult>

  fetchOverrides(client: ApiClient, id: string): Promise<WorkspaceOverride[]>
  saveOverride(
    client: ApiClient,
    id: string,
    body: MCPOverrideUpdateBody,
  ): Promise<WorkspaceOverride[]>

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
  connectors: [],
  loading: false,
  error: null,
  selectedId: null,
  pendingOAuthInstallId: null,
  overrideCounts: new Map(),

  async fetchAll(client, wsId) {
    set({ loading: true, error: null })
    try {
      const [servers, catalogItems] = await Promise.all([
        api.adminListServers(client),
        api.wsCatalogList(client, wsId).catch(() => [] as MCPCatalogConnector[]),
      ])

      const overrideCounts = new Map<string, number>()
      for (const srv of servers) {
        if (srv.owner_workspace_id !== null) continue
        try {
          const overrides = await api.adminGetOverrides(client, srv.id)
          overrideCounts.set(srv.id, overrides.filter((o) => o.enabled).length)
        } catch {
          // ignore
        }
      }

      const connectors = mergeConnectors(catalogItems, servers, overrideCounts)
      set({ connectors, overrideCounts })
    } catch (err) {
      set({ error: toCatalogError(err) })
    } finally {
      set({ loading: false })
    }
  },

  setSelectedId(id) {
    set({ selectedId: id })
  },

  async createCustom(client, body) {
    const created = await api.adminCreateServer(client, body)
    const connector: MCPAdminConnector = {
      kind: 'custom',
      id: created.id,
      name: created.name,
      provider: '',
      description: '',
      server_url: created.server_url,
      transport: created.transport,
      installed: true,
      server: created,
      authed: created.authed,
      tool_count: created.tools_cache?.length ?? 0,
      workspace_count: 0,
      last_error: created.last_error,
    }
    set({
      connectors: [...get().connectors, connector],
      selectedId: created.id,
    })
    return created
  },

  async updateServer(client, id, body) {
    const updated = await api.adminPatchServer(client, id, body)
    set({
      connectors: get().connectors.map((c) =>
        c.id === id || c.server?.id === id
          ? {
              ...c,
              server: updated,
              name: updated.name,
              authed: updated.authed,
              tool_count: updated.tools_cache?.length ?? 0,
              last_error: updated.last_error,
            }
          : c,
      ),
    })
    return updated
  },

  async deleteServer(client, id, _wsId) {
    await api.adminDeleteServer(client, id)
    set({
      connectors: get().connectors.filter((c) => c.id !== id && c.server?.id !== id),
      selectedId: get().selectedId === id ? null : get().selectedId,
    })
  },

  async refreshTools(client, id) {
    const refreshed = await api.adminRefreshTools(client, id)
    set({
      connectors: get().connectors.map((c) =>
        c.id === id || c.server?.id === id
          ? {
              ...c,
              server: refreshed,
              authed: refreshed.authed,
              tool_count: refreshed.tools_cache?.length ?? 0,
              last_error: refreshed.last_error,
            }
          : c,
      ),
    })
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

  async installFromCatalog(client, wsId, catalogId, body) {
    set({ error: null })
    try {
      const result = await api.adminCatalogInstall(client, catalogId, body)
      if (result.requires_oauth) {
        set({ pendingOAuthInstallId: result.install_id })
      }
      await get().fetchAll(client, wsId)
      set({ selectedId: result.install_id })
      return result
    } catch (err) {
      set({ error: toCatalogError(err) })
      throw err
    }
  },

  async patchInstall(client, wsId, installId, body) {
    set({ error: null })
    try {
      const result = await api.adminCatalogPatchInstall(client, installId, body)
      if (result.requires_oauth) {
        set({ pendingOAuthInstallId: result.install_id })
      }
      await get().fetchAll(client, wsId)
      return result
    } catch (err) {
      set({ error: toCatalogError(err) })
      throw err
    }
  },

  async deleteInstall(client, wsId, installId) {
    set({ error: null })
    try {
      await api.adminCatalogDeleteInstall(client, installId)
      await get().fetchAll(client, wsId)
    } catch (err) {
      set({ error: toCatalogError(err) })
      throw err
    }
  },

  async startOAuth(client, installId) {
    set({ error: null })
    try {
      const result = await api.adminOAuthStart(client, installId)
      set({ pendingOAuthInstallId: installId })
      return result
    } catch (err) {
      set({ error: toCatalogError(err) })
      throw err
    }
  },

  clearPendingOAuth() {
    set({ pendingOAuthInstallId: null })
  },

  reset() {
    set({
      connectors: [],
      loading: false,
      error: null,
      selectedId: null,
      pendingOAuthInstallId: null,
      overrideCounts: new Map(),
    })
  },
}))
