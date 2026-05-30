import { toApiError } from './client';
import { wsListEffectiveConnectors } from './mcp';
export async function getAgentConfig(client) {
    const res = await client.get('/api/v1/settings/agent');
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function updateAgentConfig(client, patch) {
    const res = await client.put('/api/v1/settings/agent', patch);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function listWorkspaceSkills(client) {
    const res = await client.get('/api/v1/settings/skills');
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function toggleWorkspaceSkill(client, installId, enabled) {
    const res = await client.patch(`/api/v1/settings/skills/${installId}`, { enabled });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function installWorkspaceSkill(client, skillId, version) {
    const res = await client.post('/api/v1/settings/skills', { skill_id: skillId, version });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function deleteWorkspaceSkill(client, installId) {
    const res = await client.del(`/api/v1/settings/skills/${installId}`);
    if (!res.ok)
        throw await toApiError(res);
}
/**
 * Four-layer effective connector list for a workspace.
 */
export async function listWorkspaceMCPConnectors(client, wsId) {
    const data = await wsListEffectiveConnectors(client, wsId);
    return data.items;
}
//# sourceMappingURL=workspace-settings.js.map