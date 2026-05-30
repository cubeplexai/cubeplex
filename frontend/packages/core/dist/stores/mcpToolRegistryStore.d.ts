import type { ApiClient } from '../api/client';
import { type MCPActiveTool } from '../api/mcp';
export interface McpToolRegistryStore {
    /** Active tools indexed by workspaceId, then by namespaced_name. */
    byWorkspace: Record<string, Record<string, MCPActiveTool>>;
    /** Per-workspace loading flag. */
    loading: Record<string, boolean>;
    /** Fetch the active-tools list for a workspace + populate the index. */
    loadForWorkspace: (client: ApiClient, workspaceId: string) => Promise<void>;
    /** Drop a workspace's entries (called on workspace switch). */
    clearWorkspace: (workspaceId: string) => void;
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
    lookup: (namespacedName: string, workspaceId?: string) => MCPActiveTool | null;
}
export declare const useMcpToolRegistryStore: import("zustand").UseBoundStore<import("zustand").StoreApi<McpToolRegistryStore>>;
//# sourceMappingURL=mcpToolRegistryStore.d.ts.map