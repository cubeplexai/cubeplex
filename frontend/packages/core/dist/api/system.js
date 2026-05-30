import { toApiError } from './client';
export async function fetchSystemInfo(client) {
    const res = await client.get('/api/v1/system/info');
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function postSetup(client, body) {
    const res = await client.post('/api/v1/system/setup', body);
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
//# sourceMappingURL=system.js.map