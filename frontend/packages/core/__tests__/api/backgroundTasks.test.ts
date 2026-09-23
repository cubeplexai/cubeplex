import { describe, expect, it, vi } from 'vitest'

import {
  listBackgroundTaskEvents,
  listBackgroundTasks,
  stopBackgroundTask,
} from '../../src/api/backgroundTasks'
import type { ApiClient } from '../../src/api/client'

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

describe('background task API', () => {
  it('uses repeated task IDs so terminal snapshots remain bounded and explicit', async () => {
    const client = {
      get: vi.fn().mockResolvedValue(response({ items: [] })),
    } as unknown as ApiClient

    await listBackgroundTasks(client, 'conv / 1', ['bgt-1', 'bgt-2'])

    expect(client.get).toHaveBeenCalledWith(
      '/api/v1/conversations/conv%20%2F%201/background-tasks?task_ids=bgt-1&task_ids=bgt-2',
    )
  })

  it('accepts a 202 stop receipt without treating cleanup as complete', async () => {
    const body = {
      accepted: true,
      cleanup_pending: true,
      remote_cancel_supported: false,
      task: { id: 'bgt-1', state: 'running' },
    }
    const client = {
      post: vi.fn().mockResolvedValue(response(body, 202)),
    } as unknown as ApiClient

    const result = await stopBackgroundTask(client, 'conv-1', 'bgt-1')

    expect(client.post).toHaveBeenCalledWith(
      '/api/v1/conversations/conv-1/background-tasks/bgt-1/stop',
      {},
    )
    expect(result.cleanup_pending).toBe(true)
  })

  it('keeps event delivery and cursor filters in the opaque paging request', async () => {
    const client = {
      get: vi.fn().mockResolvedValue(response({ items: [], next_cursor: null, has_more: false })),
    } as unknown as ApiClient

    await listBackgroundTaskEvents(client, 'conv-1', {
      delivery: 'all',
      cursor: 'opaque cursor',
      limit: 25,
    })

    expect(client.get).toHaveBeenCalledWith(
      '/api/v1/conversations/conv-1/background-task-events?delivery=all&cursor=opaque+cursor&limit=25',
    )
  })
})
