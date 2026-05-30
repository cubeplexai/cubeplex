import { toApiError } from './client';
export async function listScheduledTasks(client) {
    const res = await client.get('/api/v1/scheduled-tasks');
    if (!res.ok)
        throw await toApiError(res);
    const data = (await res.json());
    return data.tasks;
}
export async function getScheduledTask(client, id) {
    const res = await client.get(`/api/v1/scheduled-tasks/${id}`);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function createScheduledTask(client, body) {
    const res = await client.post('/api/v1/scheduled-tasks', body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function patchScheduledTask(client, id, body) {
    const res = await client.patch(`/api/v1/scheduled-tasks/${id}`, body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function pauseScheduledTask(client, id) {
    const res = await client.post(`/api/v1/scheduled-tasks/${id}/pause`, {});
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function resumeScheduledTask(client, id) {
    const res = await client.post(`/api/v1/scheduled-tasks/${id}/resume`, {});
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function deleteScheduledTask(client, id) {
    const res = await client.del(`/api/v1/scheduled-tasks/${id}`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function listScheduledTaskRuns(client, id) {
    const res = await client.get(`/api/v1/scheduled-tasks/${id}/runs`);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
//# sourceMappingURL=scheduled-tasks.js.map