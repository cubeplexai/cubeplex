import { type ApiClient } from './client';
export interface Workspace {
    id: string;
    name: string;
    org_id: string;
    role?: 'admin' | 'member';
    /** ISO-8601 with UTC offset, or null if the workspace has no conversations yet. */
    last_activity_at?: string | null;
}
export declare function listWorkspaces(client: ApiClient): Promise<Workspace[]>;
export declare function createWorkspace(client: ApiClient, input: {
    name: string;
    orgId: string;
}): Promise<Workspace>;
//# sourceMappingURL=workspaces.d.ts.map