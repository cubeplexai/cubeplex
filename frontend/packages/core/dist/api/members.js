import { toApiError } from './client';
export async function listOrgMembers(client) {
    const res = await client.get('/api/v1/admin/members');
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function addOrgMember(client, email, role) {
    const res = await client.post('/api/v1/admin/members', { email, role });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function updateOrgMemberRole(client, userId, role) {
    const res = await client.patch(`/api/v1/admin/members/${userId}/role`, { role });
    if (!res.ok)
        throw await toApiError(res);
}
export async function removeOrgMember(client, userId) {
    const res = await client.del(`/api/v1/admin/members/${userId}`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function listWsMembers(client, wsId) {
    const res = await client.get(`/api/v1/ws/${wsId}/members`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function listAvailableMembers(client, wsId) {
    const res = await client.get(`/api/v1/ws/${wsId}/members/available`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function addWsMember(client, wsId, userId, role) {
    const res = await client.post(`/api/v1/ws/${wsId}/members`, { user_id: userId, role });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function updateWsMemberRole(client, wsId, userId, role) {
    const res = await client.patch(`/api/v1/ws/${wsId}/members/${userId}/role`, { role });
    if (!res.ok)
        throw await toApiError(res);
}
export async function removeWsMember(client, wsId, userId) {
    const res = await client.del(`/api/v1/ws/${wsId}/members/${userId}`);
    if (!res.ok)
        throw await toApiError(res);
}
//# sourceMappingURL=members.js.map