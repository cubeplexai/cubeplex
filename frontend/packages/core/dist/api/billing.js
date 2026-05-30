import { toApiError } from './client';
export async function fetchCostSummary(client, params = {}) {
    const query = new URLSearchParams();
    if (params.from)
        query.set('from_date', params.from);
    if (params.to)
        query.set('to_date', params.to);
    const res = await client.get(`/api/v1/admin/cost/summary?${query}`);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function fetchWorkspaceCost(client, wsId, params = {}) {
    const query = new URLSearchParams();
    if (params.from)
        query.set('from_date', params.from);
    if (params.to)
        query.set('to_date', params.to);
    if (params.group_by)
        query.set('group_by', params.group_by);
    const res = await client.get(`/api/v1/admin/cost/by-workspace/${wsId}?${query}`);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export function buildExportUrl(wsId, params = {}) {
    const query = new URLSearchParams();
    if (params.from)
        query.set('from_date', params.from);
    if (params.to)
        query.set('to_date', params.to);
    const base = wsId
        ? `/api/v1/admin/cost/by-workspace/${wsId}/export.csv`
        : '/api/v1/admin/cost/export.csv';
    return `${base}?${query}`;
}
export async function fetchCostTimeseries(client, params) {
    const query = new URLSearchParams();
    query.set('dimension', params.dimension);
    if (params.granularity)
        query.set('granularity', params.granularity);
    if (params.from)
        query.set('from_date', params.from);
    if (params.to)
        query.set('to_date', params.to);
    if (params.workspace_ids && params.workspace_ids.length) {
        query.set('workspace_ids', params.workspace_ids.join(','));
    }
    if (params.models && params.models.length) {
        query.set('models', params.models.join(','));
    }
    const res = await client.get(`/api/v1/admin/cost/timeseries?${query}`);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
//# sourceMappingURL=billing.js.map