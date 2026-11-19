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
    getConversationBootstrap: vi.fn(),
  }
})

import { ApiError, getConversationBootstrap, streamMessages, streamRun } from '../../src/api'
import { useMessageStore } from '../../src/stores/messageStore'
import { getTextContent } from '../../src/types'

const CONV = 'conv-reconnect'
const fakeClient = { resolvePath: (path: string) => path } as never

function delta(content: string, runId = 'run-1'): AgentEvent {
  return {
    type: 'text_delta',
    timestamp: new Date().toISOString(),
    data: { content },
    agent_id: null,
    agent_name: null,
    run_id: runId,
  }
}

function doneEvent(): AgentEvent {
  return {
    type: 'done',
    timestamp: new Date().toISOString(),
    data: {},
    agent_id: null,
    agent_name: null,
  }
}

function errorEvent(message: string): AgentEvent {
  return {
    type: 'error',
    timestamp: new Date().toISOString(),
    data: { error_code: 'internal_error', message },
    agent_id: null,
    agent_name: null,
  }
}

describe('messageStore transport drop', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.getState().clearStream()
    useMessageStore.setState({
      messages: { [CONV]: [] },
      errors: {},
      runLifecycle: {},
      streamConnection: null,
    })
  })

  it('reattaches via streamRun after the POST stream ends mid-run', async () => {
    vi.mocked(streamMessages).mockImplementation(async function* (_c, _id, _content, _a, _s, opts) {
      opts?.onRunId?.('run-1')
      yield delta('Hi')
    })
    vi.mocked(streamRun).mockImplementation(async function* () {
      yield delta(' there')
      yield doneEvent()
    })

    await useMessageStore.getState().send(fakeClient, CONV, 'hello')

    expect(streamRun).toHaveBeenCalled()
    const msgs = useMessageStore.getState().messages[CONV] ?? []
    const assistant = msgs.find((m) => m.role === 'assistant')
    expect(assistant && getTextContent(assistant)).toBe('Hi there')
    expect(useMessageStore.getState().isStreaming).toBe(false)
    expect(useMessageStore.getState().errors[CONV] ?? null).toBeNull()
    expect(useMessageStore.getState().streamConnection).toBeNull()
  })

  it('does not mark a pre-run-id drop as a model failure', async () => {
    vi.mocked(streamMessages).mockImplementation(async function* () {
      yield* []
      throw new Error('Failed to fetch')
    })

    await useMessageStore.getState().send(fakeClient, CONV, 'hello')

    const state = useMessageStore.getState()
    expect(state.errors[CONV] ?? null).toBeNull()
    expect(state.isStreaming).toBe(false)
    expect(state.streamConnection).toBeNull()
    const msgs = state.messages[CONV] ?? []
    expect(msgs.every((m) => m.role === 'user')).toBe(true)
    expect(msgs.some((m) => m.role === 'assistant')).toBe(false)
  })

  it('ignores a server error event while the user is cancelling', async () => {
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.mocked(streamMessages).mockImplementation(async function* (_c, _id, _content, _a, _s, opts) {
      opts?.onRunId?.('run-1')
      yield delta('partial')
      await held
      yield errorEvent('Run cancelled')
    })

    const sendPromise = useMessageStore.getState().send(fakeClient, CONV, 'hello')
    await Promise.resolve()
    useMessageStore.setState({
      cancellingConversationIds: { [CONV]: true },
    })
    release()
    await sendPromise

    const state = useMessageStore.getState()
    const assistant = (state.messages[CONV] ?? []).find((m) => m.role === 'assistant')
    expect(assistant?.stop_reason).not.toBe('error')
    expect(state.errors[CONV] ?? null).toBeNull()
  })

  it('reconciles without flashing Reconnecting on EOF/404 after run completion', async () => {
    vi.useFakeTimers()
    const observedConnections: Array<string | null> = []
    const unsub = useMessageStore.subscribe((s) => {
      observedConnections.push(s.streamConnection)
    })
    const completedBootstrap = {
      bootstrap: {
        messages: [
          {
            id: 'u-1',
            role: 'user',
            content: [{ type: 'text', text: 'hello' }],
          },
          {
            id: 'a-1',
            role: 'assistant',
            run_id: 'run-eof',
            stop_reason: 'stop',
            content: [{ type: 'text', text: 'complete answer' }],
          },
        ],
        oldest_seq: 1,
        has_more: false,
        todos: null,
        active_run: null,
        pending_hitl: null,
        last_run_status: 'completed',
        execution_generation: 0,
        stop_all: null,
        run_control: null,
        background_summary: {
          has_inflight: false,
          has_pending: false,
          has_cleanup: false,
          can_stop: false,
        },
        background_events: { items: [], next_cursor: null, has_more: false },
      } as never,
      etag: null,
    }
    try {
      vi.mocked(streamMessages).mockImplementation(
        async function* (_c, _id, _content, _a, _s, opts) {
          opts?.onRunId?.('run-eof')
          yield delta('complete answer', 'run-eof')
        },
      )
      vi.mocked(streamRun).mockImplementation(async function* () {
        yield* []
      })
      vi.mocked(getConversationBootstrap).mockResolvedValue(completedBootstrap)

      const sendPromise = useMessageStore.getState().send(fakeClient, CONV, 'hello')
      await vi.advanceTimersByTimeAsync(1_000)
      await sendPromise
      await vi.runAllTimersAsync()

      expect(observedConnections).not.toContain('reconnecting')
      expect(useMessageStore.getState().streamConnection).toBeNull()
      expect(useMessageStore.getState().isStreaming).toBe(false)

      // 404 on streamRun reconciles immediately without backoff or Reconnecting
      observedConnections.length = 0
      vi.mocked(streamRun).mockImplementation(async function* () {
        yield* []
        throw new ApiError('Run not found', 404)
      })
      await useMessageStore.getState().send(fakeClient, CONV, 'hello again')
      await vi.runAllTimersAsync()

      expect(observedConnections).not.toContain('reconnecting')
      expect(useMessageStore.getState().streamConnection).toBeNull()
      expect(useMessageStore.getState().isStreaming).toBe(false)
    } finally {
      unsub()
      vi.useRealTimers()
    }
  })

  it('clears stale reconnecting state on expired-Redis pending_hitl bootstrap', async () => {
    useMessageStore.setState({
      streamConnection: 'reconnecting',
    })
    vi.mocked(getConversationBootstrap).mockResolvedValueOnce({
      bootstrap: {
        messages: [],
        oldest_seq: null,
        has_more: false,
        todos: null,
        active_run: null,
        pending_hitl: {
          run_id: 'run-hitl',
          question_id: 'q-1',
          kind: 'ask_user',
          requested_at: '2026-11-18T00:00:00Z',
          questions: [],
        },
        last_run_status: null,
        execution_generation: 0,
        stop_all: null,
        run_control: null,
        background_summary: {
          has_inflight: false,
          has_pending: false,
          has_cleanup: false,
          can_stop: false,
        },
        background_events: { items: [], next_cursor: null, has_more: false },
      } as never,
      etag: null,
    })

    await useMessageStore.getState().loadMessages(fakeClient, CONV, { force: true })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(streamRun).not.toHaveBeenCalled()
    expect(useMessageStore.getState().streamConnection).toBeNull()
    expect(useMessageStore.getState().currentRunId).toBe('run-hitl')
  })
})
