/**
 * Workspace-scoped MCP catalog state.
 *
 * Powers the workspace settings MCP panel: lists every active catalog
 * connector with per-(workspace, user) status (visible / installed-org-wide /
 * installed-workspace-private / available-to-install) and exposes the four
 * write actions a workspace member can take:
 *
 *   - install a catalog connector workspace-private (POST /ws/.../mcp/catalog/.../install)
 *   - uninstall a workspace-private install (DELETE /ws/.../mcp/installs/...)
 *   - enable an org-wide install for this workspace (PATCH .../override enabled=true)
 *   - disable an org-wide install for this workspace (PATCH .../override enabled=false)
 *
 * Distinct from `useMcpStore` (admin-scoped) — both read the same backend
 * surfaces but the admin store deals in org-wide installs + workspace
 * override matrices, while this one deals in a single workspace's view.
 */

import { create } from 'zustand'

import type { ApiClient } from '../api/client'
import * as api from '../api/mcp'
import type { MCPCatalogConnector, MCPCatalogInstallWsRequest } from '../types/mcp'

export interface WorkspaceMcpCatalogStore {
  connectors: MCPCatalogConnector[]
  loading: boolean
  error: string | null
  selectedSlug: string | null

  load: (client: ApiClient, wsId: string) => Promise<void>
  selectSlug: (slug: string | null) => void

  installForWorkspace: (
    client: ApiClient,
    wsId: string,
    catalogId: string,
    body: MCPCatalogInstallWsRequest,
  ) => Promise<void>

  uninstallWorkspacePrivate: (client: ApiClient, wsId: string, installId: string) => Promise<void>

  enableOrgInstall: (client: ApiClient, wsId: string, installId: string) => Promise<void>
  disableOrgInstall: (client: ApiClient, wsId: string, installId: string) => Promise<void>
}

export const useWorkspaceMcpCatalogStore = create<WorkspaceMcpCatalogStore>((set, get) => ({
  connectors: [],
  loading: false,
  error: null,
  selectedSlug: null,

  async load(client, wsId) {
    set({ loading: true, error: null })
    try {
      const items = await api.wsCatalogList(client, wsId)
      set({ connectors: items, loading: false })
    } catch (err) {
      set({ loading: false, error: String(err) })
    }
  },

  selectSlug(slug) {
    set({ selectedSlug: slug })
  },

  async installForWorkspace(client, wsId, catalogId, body) {
    await api.wsCatalogInstall(client, wsId, catalogId, body)
    await get().load(client, wsId)
  },

  async uninstallWorkspacePrivate(client, wsId, installId) {
    await api.wsCatalogDeleteInstall(client, wsId, installId)
    await get().load(client, wsId)
  },

  async enableOrgInstall(client, wsId, installId) {
    await api.wsCatalogOverrideOrgInstall(client, wsId, installId, { enabled: true })
    await get().load(client, wsId)
  },

  async disableOrgInstall(client, wsId, installId) {
    await api.wsCatalogOverrideOrgInstall(client, wsId, installId, { enabled: false })
    await get().load(client, wsId)
  },
}))
