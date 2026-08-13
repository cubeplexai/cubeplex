import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentEvent } from '../../src/types'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    streamMessages: vi.fn(),
    streamRun: vi.fn(() =>
      (async function* () {
        yield* []
      })(),
    ),
    steerRun: vi.fn().mockResolvedValue({ status: 'queued', run_id: 'r1' }),
  }
})

import { ApiError, steerRun, streamMessages, streamRun } from '../../src/api'
import { useMessageStore } from '../../src/stores/messageStore'

const fakeClient = { resolvePath: (path: string) => path } as never

function activeRunConflict(): AsyncGenerator<AgentEvent> {
  return (async function* () {
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: {
        error_code: 'active_run_conflict',
        message: 'Previous turn is still finishing.',
      },
      agent_id: null,
      agent_name: null,
    } as AgentEvent
  })()
}

function terminalError(): AsyncGenerator<AgentEvent> {
  return (async function* () {
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: { error_code: 'internal_error', message: 'run failed' },
      agent_id: null,
      agent_name: null,
    } as AgentEvent
  })()
}

function bootstrapResponse(active = true): Promise<Response> {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        messages: [],
        active_run: active ? { run_id: 'r1', status: 'running' } : null,
        pending_hitl: null,
        pending_steers: [],
        last_run_status: null,
      }),
  } as Response)
}

describe('messageStore.send active-run conflict', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(streamMessages).mockImplementation(activeRunConflict)
    useMessageStore.setState({
      messages: { conv1: [] },
      streamAgents: {},
      isStreaming: false,
      streamingConversationId: null,
      currentRunId: null,
      errors: {},
      runLifecycle: {},
    })
  })

  it('keeps attachment sends as a typed conflict instead of steering them', async () => {
    await expect(
      useMessageStore.getState().send(fakeClient, 'conv1', 'keep my draft', ['file-1']),
    ).rejects.toEqual(
      expect.objectContaining<ApiError>({ status: 409, code: 'active_run_conflict' }),
    )

    expect(useMessageStore.getState().messages.conv1).toEqual([])
    expect(useMessageStore.getState().errors.conv1 ?? null).toBeNull()
  })

  it('refreshes stale lifecycle and reroutes a text-only conflict through steering', async () => {
    const client = {
      resolvePath: (path: string) => path,
      get: vi.fn().mockImplementation(() => bootstrapResponse()),
    } as never

    await useMessageStore.getState().send(client, 'conv1', 'queue this instead')

    expect(streamMessages).toHaveBeenCalledTimes(1)
    expect(steerRun).toHaveBeenCalledWith(client, 'conv1', 'queue this instead', expect.any(String))
  })

  it('propagates a failed conflict reroute so the composer can restore the draft', async () => {
    const client = {
      resolvePath: (path: string) => path,
      get: vi.fn().mockImplementation(() => bootstrapResponse()),
    } as never
    vi.mocked(steerRun).mockRejectedValueOnce(
      new ApiError('Steering queue is full.', 409, 'steer_queue_full', null),
    )

    await expect(
      useMessageStore.getState().send(client, 'conv1', 'do not lose this text'),
    ).rejects.toEqual(expect.objectContaining({ code: 'steer_queue_full' }))
  })

  it('returns the lifecycle to idle after a terminal send error', async () => {
    vi.mocked(streamMessages).mockImplementationOnce(terminalError)

    await useMessageStore.getState().send(fakeClient, 'conv1', 'will fail')

    expect(useMessageStore.getState().runLifecycle.conv1).toBe('idle')
  })

  it('returns the lifecycle to idle after a terminal reattach error', async () => {
    vi.mocked(streamRun).mockImplementationOnce(terminalError)
    const client = {
      resolvePath: (path: string) => path,
      get: vi.fn().mockImplementation(() => bootstrapResponse()),
    } as never

    await useMessageStore.getState().loadMessages(client, 'conv1')
    await vi.waitFor(() => {
      expect(useMessageStore.getState().errors.conv1).not.toBeNull()
    })

    expect(useMessageStore.getState().runLifecycle.conv1).toBe('idle')
  })
})
