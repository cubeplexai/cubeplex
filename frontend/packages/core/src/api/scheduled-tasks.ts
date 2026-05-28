import type {
  ScheduledTaskCreate,
  ScheduledTaskOut,
  ScheduledTaskPatch,
  ScheduledTaskRunOut,
} from '../types/scheduled-task'
import { toApiError, type ApiClient } from './client'

export async function listScheduledTasks(client: ApiClient): Promise<ScheduledTaskOut[]> {
  const res = await client.get('/api/v1/scheduled-tasks')
  if (!res.ok) throw await toApiError(res)
  const data = (await res.json()) as { tasks: ScheduledTaskOut[] }
  return data.tasks
}

export async function getScheduledTask(client: ApiClient, id: string): Promise<ScheduledTaskOut> {
  const res = await client.get(`/api/v1/scheduled-tasks/${id}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ScheduledTaskOut>
}

export async function createScheduledTask(
  client: ApiClient,
  body: ScheduledTaskCreate,
): Promise<ScheduledTaskOut> {
  const res = await client.post('/api/v1/scheduled-tasks', body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ScheduledTaskOut>
}

export async function patchScheduledTask(
  client: ApiClient,
  id: string,
  body: ScheduledTaskPatch,
): Promise<ScheduledTaskOut> {
  const res = await client.patch(`/api/v1/scheduled-tasks/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ScheduledTaskOut>
}

export async function pauseScheduledTask(client: ApiClient, id: string): Promise<ScheduledTaskOut> {
  const res = await client.post(`/api/v1/scheduled-tasks/${id}/pause`, {})
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ScheduledTaskOut>
}

export async function resumeScheduledTask(
  client: ApiClient,
  id: string,
): Promise<ScheduledTaskOut> {
  const res = await client.post(`/api/v1/scheduled-tasks/${id}/resume`, {})
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ScheduledTaskOut>
}

export async function deleteScheduledTask(client: ApiClient, id: string): Promise<void> {
  const res = await client.del(`/api/v1/scheduled-tasks/${id}`)
  if (!res.ok) throw await toApiError(res)
}

export async function listScheduledTaskRuns(
  client: ApiClient,
  id: string,
): Promise<ScheduledTaskRunOut[]> {
  const res = await client.get(`/api/v1/scheduled-tasks/${id}/runs`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ScheduledTaskRunOut[]>
}
