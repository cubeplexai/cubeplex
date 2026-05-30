import { toApiError } from './client';
export async function discoverSkills(client, wsId, q, limit = 5) {
    const params = new URLSearchParams({ q, limit: String(limit) });
    const res = await client.get(`/api/v1/ws/${wsId}/skills/discover?${params}`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function installSkill(client, wsId, candidateId) {
    const res = await client.post(`/api/v1/ws/${wsId}/skills/install`, {
        candidate_id: candidateId,
    });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function refreshSkill(client, wsId, skillId) {
    const res = await client.post(`/api/v1/ws/${wsId}/skills/${skillId}/refresh`, null);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
//# sourceMappingURL=skills.js.map