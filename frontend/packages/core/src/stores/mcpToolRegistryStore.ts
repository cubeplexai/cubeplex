// frontend/packages/core/src/stores/mcpToolRegistryStore.ts
//
// Per-workspace registry mapping ``namespaced_name`` (the string that
// arrives on ``tool_call.name`` SSE events for MCP tools, e.g.
// ``Linear__create_issue``) to display metadata captured at MCP
// discovery time (server logo + per-tool icon + the bare tool name
// stripped of its install slug).
//
// The chat UI's ToolCallItem / ToolDetailPanel use this to swap the
// raw ``WebTools__web_search``-style label for a server icon + bare
// name + ``Server · tool`` tooltip. When the registry has no entry
// for a name, the UI falls back to the existing internal-tool icon
// mapping — so the registry is purely additive over today's render.
//
// Loaded once per workspace mount (mirrors how the artifact store is
// primed) and cleared on workspace switch.
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { wsListActiveTools, type MCPActiveTool } from '../api/mcp'

export interface McpToolRegistryStore {
  /** Active tools indexed by workspaceId, then by namespaced_name. */
  byWorkspace: Record<string, Record<string, MCPActiveTool>>

  /** Per-workspace loading flag. */
  loading: Record<string, boolean>

  /** Fetch the active-tools list for a workspace + populate the index. */
  loadForWorkspace: (client: ApiClient, workspaceId: string) => Promise<void>

  /** Drop a workspace's entries (called on workspace switch). */
  clearWorkspace: (workspaceId: string) => void

  /**
   * Lookup an entry by its ``namespaced_name``. Returns ``null`` for
   * internal tools (whose name never contains ``__``), for entries
   * recently installed but not yet loaded, and for stale entries
   * removed from the catalog — the caller falls back to the lucide
   * tool-icon registry.
   *
   * When ``workspaceId`` is omitted, looks across all loaded workspaces;
   * this keeps the call site simple in components that don't have the
   * workspace id handy. The first match wins; namespaced names are
   * unique per workspace and rare-but-not-impossible across workspaces.
   */
  lookup: (namespacedName: string, workspaceId?: string) => MCPActiveTool | null
}

export const useMcpToolRegistryStore = create<McpToolRegistryStore>((set, get) => ({
  byWorkspace: {},
  loading: {},

  async loadForWorkspace(client, workspaceId) {
    if (get().loading[workspaceId]) return
    set((s) => ({ loading: { ...s.loading, [workspaceId]: true } }))
    try {
      const { items } = await wsListActiveTools(client, workspaceId)
      const index: Record<string, MCPActiveTool> = {}
      for (const it of items) {
        index[it.namespaced_name] = it
      }
      set((s) => ({
        byWorkspace: { ...s.byWorkspace, [workspaceId]: index },
      }))
    } catch {
      // Tool icons are a display nicety; never break the chat UI if
      // the registry call fails. The render path falls back to the
      // existing internal-tool icon map.
    } finally {
      set((s) => ({ loading: { ...s.loading, [workspaceId]: false } }))
    }
  },

  clearWorkspace(workspaceId) {
    set((s) => {
      const { [workspaceId]: _, ...rest } = s.byWorkspace
      return { byWorkspace: rest }
    })
  },

  lookup(namespacedName, workspaceId) {
    if (!namespacedName.includes('__')) return null
    const all = get().byWorkspace
    if (workspaceId) {
      return all[workspaceId]?.[namespacedName] ?? null
    }
    for (const ws of Object.values(all)) {
      const hit = ws[namespacedName]
      if (hit) return hit
    }
    return null
  },
}))
