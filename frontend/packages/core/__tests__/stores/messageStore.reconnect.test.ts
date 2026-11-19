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
    vi.mocked(getConversationBootstrap).mockReset()
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

  it('retries transient bootstrap failure after 404 without wedging isStreaming', async () => {
    vi.useFakeTimers()
    try {
      vi.mocked(streamMessages).mockImplementation(
        async function* (_c, _id, _content, _a, _s, opts) {
          opts?.onRunId?.('run-404')
          yield* []
          throw new ApiError('Run not found', 404)
        },
      )
      vi.mocked(getConversationBootstrap)
        .mockRejectedValueOnce(new Error('502 Bad Gateway'))
        .mockResolvedValue({
          bootstrap: {
            messages: [
              {
                id: 'a-done',
                role: 'assistant',
                run_id: 'run-404',
                stop_reason: 'stop',
                content: [{ type: 'text', text: 'recovered' }],
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
        })

      const observedConnections: Array<string | null> = []
      const unsub = useMessageStore.subscribe((s) => {
        observedConnections.push(s.streamConnection)
      })
      const sendPromise = useMessageStore.getState().send(fakeClient, CONV, 'hello')
      await Promise.resolve()
      await Promise.resolve()
      // Initial 404 should not flash reconnecting before attempting bootstrap,
      // and should not issue a duplicate streamRun call on 404.
      expect(observedConnections[0]).toBe('connected')
      expect(streamRun).not.toHaveBeenCalled()

      await vi.advanceTimersByTimeAsync(1_500)
      await sendPromise

      unsub()
      const state = useMessageStore.getState()
      expect(state.isStreaming).toBe(false)
      expect(state.streamConnection).toBeNull()
      expect(state.messages[CONV]?.some((m) => m.id === 'a-done')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('resets reconnect attempt after receiving events so completion EOF stays silent', async () => {
    vi.useFakeTimers()
    const observedConnections: Array<string | null> = []
    const unsub = useMessageStore.subscribe((s) => {
      observedConnections.push(s.streamConnection)
    })
    try {
      vi.mocked(streamMessages).mockImplementation(
        async function* (_c, _id, _content, _a, _s, opts) {
          opts?.onRunId?.('run-long')
          yield delta('part 1 ', 'run-long')
        },
      )
      let call = 0
      vi.mocked(streamRun).mockImplementation(async function* () {
        call += 1
        if (call === 1) {
          // Proxy cuts idle stream once before run finishes
          yield* []
          return
        }
        if (call === 2) {
          // Reconnect succeeds and delivers a new event, then ends cleanly at EOF
          yield { ...delta('part 2', 'run-long'), event_id: '2-0' }
          return
        }
      })
      vi.mocked(getConversationBootstrap)
        .mockResolvedValueOnce({
          bootstrap: {
            messages: [],
            oldest_seq: null,
            has_more: false,
            todos: null,
            active_run: { run_id: 'run-long', status: 'running' },
            pending_hitl: null,
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
        .mockResolvedValueOnce({
          bootstrap: {
            messages: [
              {
                id: 'a-long',
                role: 'assistant',
                run_id: 'run-long',
                stop_reason: 'stop',
                content: [{ type: 'text', text: 'part 1 part 2' }],
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
        })

      const sendPromise = useMessageStore.getState().send(fakeClient, CONV, 'hello')
      await vi.advanceTimersByTimeAsync(1_000)
      await vi.advanceTimersByTimeAsync(1_000)
      await sendPromise

      expect(observedConnections).not.toContain('reconnecting')
      expect(useMessageStore.getState().isStreaming).toBe(false)
    } finally {
      unsub()
      vi.useRealTimers()
    }
  })

  it('transitions paused_hitl watcher to streaming when resumed output arrives', () => {
    useMessageStore.setState({
      isStreaming: false,
      streamingConversationId: CONV,
      currentRunId: 'run-hitl',
      streamAgents: {},
      runLifecycle: { [CONV]: 'paused_hitl' },
    })

    useMessageStore.getState().__applyEvent({
      ...delta('subagent work', 'run-hitl'),
      agent_id: 'subagent:tc-1',
    })

    expect(useMessageStore.getState().isStreaming).toBe(true)
    expect(useMessageStore.getState().runLifecycle[CONV]).toBe('running')
    expect(useMessageStore.getState().streamAgents.main).toBeDefined()
  })

  it('prevents a restricted recovery load from swallowing a foreground load', async () => {
    let resolveRestricted!: (v: unknown) => void
    vi.mocked(getConversationBootstrap)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveRestricted = resolve
        }) as never,
      )
      .mockResolvedValueOnce({
        bootstrap: {
          messages: [
            {
              id: 'm-conv-a',
              role: 'assistant',
              content: [{ type: 'text', text: 'loaded A' }],
            },
          ],
          oldest_seq: 1,
          has_more: false,
          todos: null,
          active_run: null,
          pending_hitl: null,
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

    const restrictedLoad = useMessageStore.getState().loadMessages(fakeClient, 'conv-a', {
      force: true,
      preserveOtherConversationStream: true,
    })
    await Promise.resolve()
    // conv-b starts streaming while conv-a's restricted recovery is in flight
    useMessageStore.setState({
      isStreaming: true,
      streamingConversationId: 'conv-b',
      currentRunId: 'run-b',
    })
    // User navigates to conv-a (unrestricted foreground load)
    const foregroundLoad = useMessageStore.getState().loadMessages(fakeClient, 'conv-a')
    resolveRestricted({
      bootstrap: {
        messages: [],
        oldest_seq: null,
        has_more: false,
        todos: null,
        active_run: null,
        pending_hitl: null,
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
      },
      etag: null,
    })
    await Promise.all([restrictedLoad, foregroundLoad])

    expect(useMessageStore.getState().messages['conv-a']?.[0]?.id).toBe('m-conv-a')
  })
})
