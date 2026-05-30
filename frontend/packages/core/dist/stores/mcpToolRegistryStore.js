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
import { create } from 'zustand';
import { wsListActiveTools } from '../api/mcp';
export const useMcpToolRegistryStore = create((set, get) => ({
    byWorkspace: {},
    loading: {},
    async loadForWorkspace(client, workspaceId) {
        if (get().loading[workspaceId])
            return;
        set((s) => ({ loading: { ...s.loading, [workspaceId]: true } }));
        try {
            const { items } = await wsListActiveTools(client, workspaceId);
            const index = {};
            for (const it of items) {
                index[it.namespaced_name] = it;
            }
            set((s) => ({
                byWorkspace: { ...s.byWorkspace, [workspaceId]: index },
            }));
        }
        catch {
            // Tool icons are a display nicety; never break the chat UI if
            // the registry call fails. The render path falls back to the
            // existing internal-tool icon map.
        }
        finally {
            set((s) => ({ loading: { ...s.loading, [workspaceId]: false } }));
        }
    },
    clearWorkspace(workspaceId) {
        set((s) => {
            const { [workspaceId]: _, ...rest } = s.byWorkspace;
            return { byWorkspace: rest };
        });
    },
    lookup(namespacedName, workspaceId) {
        if (!namespacedName.includes('__'))
            return null;
        const all = get().byWorkspace;
        if (workspaceId) {
            return all[workspaceId]?.[namespacedName] ?? null;
        }
        for (const ws of Object.values(all)) {
            const hit = ws[namespacedName];
            if (hit)
                return hit;
        }
        return null;
    },
}));
//# sourceMappingURL=mcpToolRegistryStore.js.map