import type { ApiClient } from '../api/client';
import { type Workspace } from '../api/workspaces';
export interface WorkspaceStore {
    workspaces: Workspace[];
    isLoading: boolean;
    error: string | null;
    fetchList(client: ApiClient): Promise<void>;
    create(client: ApiClient, name: string): Promise<Workspace>;
    reset(): void;
}
export declare const useWorkspaceStore: import("zustand").UseBoundStore<import("zustand").StoreApi<WorkspaceStore>>;
//# sourceMappingURL=workspaceStore.d.ts.map