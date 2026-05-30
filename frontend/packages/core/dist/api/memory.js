import { toApiError } from './client';
export async function listMemory(client, opts = {}) {
    const params = new URLSearchParams();
    if (opts.scope)
        params.set('scope', opts.scope);
    if (opts.type)
        params.set('type', opts.type);
    if (opts.status)
        params.set('status', opts.status);
    if (opts.q)
        params.set('q', opts.q);
    const qs = params.toString();
    const url = qs ? `/api/v1/memory?${qs}` : '/api/v1/memory';
    const res = await client.get(url);
    if (!res.ok)
        throw await toApiError(res);
    const data = (await res.json());
    return data.items;
}
export async function createMemory(client, body) {
    const res = await client.post('/api/v1/memory', body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function updateMemory(client, id, body) {
    const res = await client.patch(`/api/v1/memory/${id}`, body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function archiveMemory(client, id) {
    const res = await client.del(`/api/v1/memory/${id}`);
    if (!res.ok)
        throw await toApiError(res);
}
//# sourceMappingURL=memory.js.map