import { toApiError } from './client';
export async function fetchProviders(client) {
    const res = await client.get('/api/v1/admin/providers');
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function fetchProvider(client, id) {
    const res = await client.get(`/api/v1/admin/providers/${id}`);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function createProvider(client, body) {
    const res = await client.post('/api/v1/admin/providers', body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function updateProvider(client, id, body) {
    const res = await client.patch(`/api/v1/admin/providers/${id}`, body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function deleteProvider(client, id) {
    const res = await client.del(`/api/v1/admin/providers/${id}`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function createModel(client, providerId, body) {
    const res = await client.post(`/api/v1/admin/providers/${providerId}/models`, body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function updateModel(client, providerId, modelId, body) {
    const res = await client.patch(`/api/v1/admin/providers/${providerId}/models/${modelId}`, body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function deleteModel(client, providerId, modelId) {
    const res = await client.del(`/api/v1/admin/providers/${providerId}/models/${modelId}`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function fetchOrgLLMSettings(client) {
    const res = await client.get('/api/v1/admin/settings/llm');
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function updateOrgLLMSettings(client, body) {
    const res = await client.put('/api/v1/admin/settings/llm', body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function listPresets(client) {
    const res = await client.get('/api/v1/admin/llm/presets');
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function presaveLiveness(client, body) {
    const res = await client.post('/api/v1/admin/providers/liveness', body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function presaveTest(client, body) {
    const res = await client.post('/api/v1/admin/providers/test', body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function checkLiveness(client, providerId, modelId) {
    const res = await client.post(`/api/v1/admin/providers/${providerId}/liveness`, {
        model_id: modelId,
    });
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function testModel(client, providerId, modelDbId) {
    const res = await client.post(`/api/v1/admin/providers/${providerId}/models/${modelDbId}/test`, {});
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function setModelEnabled(client, providerId, modelDbId, enabled) {
    const res = await client.patch(`/api/v1/admin/providers/${providerId}/models/${modelDbId}`, {
        enabled,
    });
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
//# sourceMappingURL=providers.js.map