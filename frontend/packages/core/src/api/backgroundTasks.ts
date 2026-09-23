import type { BackgroundTask, BackgroundTaskEventPage } from '../types/background-task'
import { toApiError, type ApiClient } from './client'

export interface BackgroundTaskListResponse {
  items: BackgroundTask[]
}

export interface StopBackgroundTaskResponse {
  accepted: boolean
  cleanup_pending: boolean
  remote_cancel_supported: boolean
  task: BackgroundTask
}

export async function listBackgroundTasks(
  client: ApiClient,
  conversationId: string,
  taskIds?: string[],
): Promise<BackgroundTask[]> {
  const params = new URLSearchParams()
  for (const taskId of taskIds ?? []) params.append('task_ids', taskId)
  const query = params.size > 0 ? `?${params.toString()}` : ''
  const res = await client.get(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/background-tasks${query}`,
  )
  if (!res.ok) throw await toApiError(res)
  return ((await res.json()) as BackgroundTaskListResponse).items
}

export async function getBackgroundTask(
  client: ApiClient,
  conversationId: string,
  taskId: string,
): Promise<BackgroundTask> {
  const res = await client.get(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/background-tasks/${encodeURIComponent(taskId)}`,
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as BackgroundTask
}

export async function stopBackgroundTask(
  client: ApiClient,
  conversationId: string,
  taskId: string,
): Promise<StopBackgroundTaskResponse> {
  const res = await client.post(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/background-tasks/${encodeURIComponent(taskId)}/stop`,
    {},
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as StopBackgroundTaskResponse
}

export async function listBackgroundTaskEvents(
  client: ApiClient,
  conversationId: string,
  options: { delivery?: 'pending' | 'all'; cursor?: string; limit?: number } = {},
): Promise<BackgroundTaskEventPage> {
  const params = new URLSearchParams()
  if (options.delivery) params.set('delivery', options.delivery)
  if (options.cursor) params.set('cursor', options.cursor)
  if (options.limit != null) params.set('limit', String(options.limit))
  const query = params.size > 0 ? `?${params.toString()}` : ''
  const res = await client.get(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/background-task-events${query}`,
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as BackgroundTaskEventPage
}
