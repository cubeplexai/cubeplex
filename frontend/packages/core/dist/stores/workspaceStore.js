import { create } from 'zustand';
import { listWorkspaces, createWorkspace } from '../api/workspaces';
// One-user-one-org M1 assumption: a new workspace is created under the first
// workspace's org_id. When multi-org-per-user ships (P2), pass an explicit
// org id instead of reusing the first-seen one.
export const useWorkspaceStore = create((set, get) => ({
    workspaces: [],
    isLoading: false,
    error: null,
    async fetchList(client) {
        set({ isLoading: true, error: null });
        try {
            const workspaces = await listWorkspaces(client);
            set({ workspaces });
        }
        catch (err) {
            set({ error: err.message });
        }
        finally {
            set({ isLoading: false });
        }
    },
    async create(client, name) {
        const existing = get().workspaces;
        if (existing.length === 0) {
            throw new Error('Cannot create workspace: load workspaces first to determine org_id');
        }
        const orgId = existing[0].org_id;
        const ws = await createWorkspace(client, { name, orgId });
        set((s) => ({ workspaces: [ws, ...s.workspaces] }));
        return ws;
    },
    reset() {
        set({ workspaces: [], isLoading: false, error: null });
    },
}));
//# sourceMappingURL=workspaceStore.js.map