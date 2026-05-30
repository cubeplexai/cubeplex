import { toApiError } from './client';
export async function listWorkspaces(client) {
    const res = await client.get('/api/v1/workspaces');
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function createWorkspace(client, input) {
    const res = await client.post('/api/v1/workspaces', { name: input.name, org_id: input.orgId });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
//# sourceMappingURL=workspaces.js.map