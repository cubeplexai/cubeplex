// Four-layer MCP API helpers (templates / installs / state / connectors / grants).
import { toApiError } from './client';
// ---------------- Templates (public + admin) ---------------- //
export async function listTemplates(client) {
    const res = await client.get('/api/v1/mcp/templates');
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function wsListTemplates(client, wsId) {
    const res = await client.get(`/api/v1/ws/${wsId}/mcp/templates`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function adminListTemplates(client) {
    const res = await client.get('/api/v1/admin/mcp/templates');
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
// ---------------- Installs (admin org-scope + workspace-scope) ---------------- //
export async function adminCreateInstall(client, body) {
    const res = await client.post('/api/v1/admin/mcp/installs', body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function adminGetInstall(client, installId) {
    const res = await client.get(`/api/v1/admin/mcp/installs/${installId}`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function adminPatchInstall(client, installId, body) {
    const res = await client.patch(`/api/v1/admin/mcp/installs/${installId}`, body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function adminDeleteInstall(client, installId) {
    const res = await client.del(`/api/v1/admin/mcp/installs/${installId}`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function wsCreateInstall(client, wsId, body) {
    const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs`, body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function wsDeleteInstall(client, wsId, installId) {
    const res = await client.del(`/api/v1/ws/${wsId}/mcp/installs/${installId}`);
    if (!res.ok)
        throw await toApiError(res);
}
// ---------------- Workspace connector state ---------------- //
export async function wsListEffectiveConnectors(client, wsId) {
    const res = await client.get(`/api/v1/ws/${wsId}/mcp/connectors`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function wsPatchConnectorState(client, wsId, installId, body) {
    const res = await client.patch(`/api/v1/ws/${wsId}/mcp/connectors/${installId}/state`, body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
// Admin / org-scope grants.
export async function adminCreateOrgGrant(client, installId, body) {
    const res = await client.post(`/api/v1/admin/mcp/installs/${installId}/grants/org`, body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function adminDeleteOrgGrant(client, installId) {
    const res = await client.del(`/api/v1/admin/mcp/installs/${installId}/grants/org`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function adminOrgGrantOAuthStart(client, installId) {
    const res = await client.post(`/api/v1/admin/mcp/installs/${installId}/grants/org/oauth/start`, {});
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
// Workspace-scope grants.
export async function wsCreateWorkspaceGrant(client, wsId, installId, body) {
    const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs/${installId}/grants/workspace`, body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function wsDeleteWorkspaceGrant(client, wsId, installId) {
    const res = await client.del(`/api/v1/ws/${wsId}/mcp/installs/${installId}/grants/workspace`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function wsWorkspaceGrantOAuthStart(client, wsId, installId) {
    const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs/${installId}/grants/workspace/oauth/start`, {});
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
// User-scope (me) grants.
export async function wsCreateMyGrant(client, wsId, installId, body) {
    const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs/${installId}/grants/me`, body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function wsDeleteMyGrant(client, wsId, installId) {
    const res = await client.del(`/api/v1/ws/${wsId}/mcp/installs/${installId}/grants/me`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function wsMyGrantOAuthStart(client, wsId, installId) {
    const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs/${installId}/grants/me/oauth/start`, {});
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function adminGetInstallEffective(client, installId) {
    const res = await client.get(`/api/v1/admin/mcp/installs/${installId}/effective`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
// ---------------- Discovery refresh ---------------- //
export async function adminRefreshDiscovery(client, installId, workspaceId) {
    const res = await client.post(`/api/v1/admin/mcp/installs/${installId}/refresh-discovery`, {
        workspace_id: workspaceId ?? null,
    });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function wsRefreshDiscovery(client, wsId, installId) {
    const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs/${installId}/refresh-discovery`, {});
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function adminInvokeTool(client, installId, toolName, args, workspaceId) {
    const res = await client.post(`/api/v1/admin/mcp/installs/${installId}/tools/${encodeURIComponent(toolName)}/invoke`, { arguments: args, workspace_id: workspaceId ?? null });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function wsInvokeTool(client, wsId, installId, toolName, args) {
    const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs/${installId}/tools/${encodeURIComponent(toolName)}/invoke`, { arguments: args });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function adminTestConnection(client, body) {
    const res = await client.post('/api/v1/admin/mcp/test-connection', body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function adminPromoteToOrg(client, installId, distribution) {
    const res = await client.post(`/api/v1/admin/mcp/installs/${installId}/promote-to-org`, {
        distribution,
    });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
// ---------------- Tool citation upsert (admin) ---------------- //
export async function adminUpsertToolCitation(client, installId, toolName, config) {
    const res = await client.put(`/api/v1/admin/mcp/installs/${installId}/tool-citations`, {
        tool_name: toolName,
        config,
    });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
// ---------------- Admin org connectors + workspace available ---------------- //
export async function adminListConnectors(client) {
    const res = await client.get('/api/v1/admin/mcp/connectors');
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function wsListAvailable(client, wsId) {
    const res = await client.get(`/api/v1/ws/${wsId}/mcp/available`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function wsListActiveTools(client, wsId) {
    const res = await client.get(`/api/v1/ws/${wsId}/mcp/active-tools`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
//# sourceMappingURL=mcp.js.map