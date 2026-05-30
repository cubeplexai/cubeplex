/**
 * Sandbox policy + workspace sandbox status API helpers.
 *
 * Admin policy editor lives at /admin/sandbox-policy (org-scope); workspace
 * sandbox status lives under the workspace path. Mirrors the workspace-
 * settings.ts shape: types + thin client wrappers, no React.
 */
import { toApiError } from './client';
export async function getSandboxPolicy(client) {
    const res = await client.get('/api/v1/admin/sandbox-policy');
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function putSandboxPolicy(client, body) {
    const res = await client.put('/api/v1/admin/sandbox-policy', body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function getWorkspaceSandboxStatus(client, wsId) {
    // Explicit /ws/{wsId}/ — bypass the ApiClient's workspaceId rewrite so this
    // helper works even when the client isn't pinned to the same workspace.
    const res = await client.get(`/api/v1/ws/${wsId}/sandbox/status`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
//# sourceMappingURL=sandboxPolicy.js.map