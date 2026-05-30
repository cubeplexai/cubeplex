import { toApiError } from './client';
export async function listTriggers(client, wsId) {
    const res = await client.get(`/api/v1/ws/${wsId}/triggers`);
    if (!res.ok)
        throw await toApiError(res);
    const data = (await res.json());
    return data.triggers;
}
export async function createTrigger(client, wsId, body) {
    const res = await client.post(`/api/v1/ws/${wsId}/triggers`, body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function getTrigger(client, wsId, id) {
    const res = await client.get(`/api/v1/ws/${wsId}/triggers/${id}`);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function updateTrigger(client, wsId, id, patch) {
    const res = await client.patch(`/api/v1/ws/${wsId}/triggers/${id}`, patch);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function deleteTrigger(client, wsId, id) {
    const res = await client.del(`/api/v1/ws/${wsId}/triggers/${id}`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function rotateSecret(client, wsId, id, body) {
    const res = await client.post(`/api/v1/ws/${wsId}/triggers/${id}/rotate-secret`, body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function listTriggerEvents(client, wsId, id, query) {
    const params = new URLSearchParams();
    if (query?.status)
        params.set('status', query.status);
    if (query?.limit !== undefined)
        params.set('limit', String(query.limit));
    if (query?.offset !== undefined)
        params.set('offset', String(query.offset));
    const qs = params.toString() ? `?${params.toString()}` : '';
    const res = await client.get(`/api/v1/ws/${wsId}/triggers/${id}/events${qs}`);
    if (!res.ok)
        throw await toApiError(res);
    const data = (await res.json());
    return data.events;
}
export async function replayEvent(client, wsId, id, eventId) {
    const res = await client.post(`/api/v1/ws/${wsId}/triggers/${id}/events/${eventId}/replay`, {});
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
//# sourceMappingURL=triggers.js.map