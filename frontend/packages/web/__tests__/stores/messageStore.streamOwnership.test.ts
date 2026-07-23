import { act } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMessageStore } from '@cubeplex/core'

const CONV_A = 'conv-A'
const CONV_B = 'conv-B'

type ControllableSSE = {
  response: () => Response
  push: (event: object) => void
  close: () => void
}

function makeControllableSSE(): ControllableSSE {
  const encoder = new TextEncoder()
  let streamController: ReadableStreamDefaultController<Uint8Array> | null = null
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller
    },
  })
  return {
    response: () =>
      new Response(stream, {
        headers: { 'content-type': 'text/event-stream' },
      }),
    push: (event: object) => {
      streamController?.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
    },
    close: () => {
      try {
        streamController?.close()
      } catch {
        // already closed / aborted
      }
    },
  }
}

const mockClient = {
  baseUrl: '',
  get: vi.fn(),
  post: vi.fn(),
  resolvePath: (p: string) => p,
}

function baseEvent(partial: object): object {
  return {
    agent_id: null,
    agent_name: null,
    timestamp: '',
    ...partial,
  }
}

async function flushMicrotasks(times = 5): Promise<void> {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  vi.useRealTimers()
  useMessageStore.setState({
    messages: {},
    streamAgents: {},
    isStreaming: false,
    streamingConversationId: null,
    currentRunId: null,
    lastAppliedEventId: null,
    statusPhase: null,
    error: null,
    errors: {},
    todos: [],
    toolStartedMap: {},
    toolResultMap: {},
    pendingConfirmMap: {},
    pendingAsk: null,
    pendingSteers: {},
  })
})

describe('messageStore stream ownership', () => {
  it('does not clear B flags when a late error arrives after A loses ownership', async () => {
    const a = makeControllableSSE()
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(a.response())),
    )

    const sendA = useMessageStore.getState().send(mockClient as never, CONV_A, 'from a')
    await flushMicrotasks()

    a.push(
      baseEvent({
        type: 'text_delta',
        data: { content: 'partial-a' },
        event_id: '1-0',
      }),
    )
    await flushMicrotasks()
    expect(useMessageStore.getState().streamingConversationId).toBe(CONV_A)
    expect(useMessageStore.getState().isStreaming).toBe(true)

    // Simulate another conversation taking the shared stream flags without
    // aborting A's fetch (so we can still deliver a late terminal event).
    useMessageStore.setState({
      isStreaming: true,
      streamingConversationId: CONV_B,
      currentRunId: 'run-B',
      streamAgents: {
        main: {
          text: 'from-b',
          toolCalls: [],
          toolResults: [],
          thinking: '',
          blocks: [],
          name: null,
        },
      },
    })

    a.push(
      baseEvent({
        type: 'error',
        data: { error_code: 'late', message: 'stale terminal from A' },
        event_id: '2-0',
      }),
    )
    await flushMicrotasks(10)
    a.close()
    await act(async () => {
      await sendA
    })

    const state = useMessageStore.getState()
    expect(state.isStreaming).toBe(true)
    expect(state.streamingConversationId).toBe(CONV_B)
    expect(state.currentRunId).toBe('run-B')
    expect(state.streamAgents.main?.text).toBe('from-b')
    // Per-conversation error for A is still allowed (optional); flags must stay on B.
  })

  it('does not clear B flags when a late done arrives after A loses ownership', async () => {
    const a = makeControllableSSE()
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(a.response())),
    )

    const sendA = useMessageStore.getState().send(mockClient as never, CONV_A, 'from a')
    await flushMicrotasks()
    a.push(
      baseEvent({
        type: 'text_delta',
        data: { content: 'partial-a' },
        event_id: '1-0',
      }),
    )
    await flushMicrotasks()

    useMessageStore.setState({
      isStreaming: true,
      streamingConversationId: CONV_B,
      currentRunId: 'run-B',
      streamAgents: {
        main: {
          text: 'from-b',
          toolCalls: [],
          toolResults: [],
          thinking: '',
          blocks: [],
          name: null,
        },
      },
    })

    a.push(baseEvent({ type: 'done', data: {}, event_id: '3-0' }))
    await flushMicrotasks(10)
    a.close()
    await act(async () => {
      await sendA
    })

    const state = useMessageStore.getState()
    expect(state.isStreaming).toBe(true)
    expect(state.streamingConversationId).toBe(CONV_B)
    expect(state.streamAgents.main?.text).toBe('from-b')
  })

  it('keeps B flags after real A→B send handoff (abort previous controller)', async () => {
    const a = makeControllableSSE()
    const b = makeControllableSSE()
    let calls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(() => {
        calls += 1
        return Promise.resolve(calls === 1 ? a.response() : b.response())
      }),
    )

    void useMessageStore.getState().send(mockClient as never, CONV_A, 'from a')
    await flushMicrotasks()
    a.push(
      baseEvent({
        type: 'text_delta',
        data: { content: 'a' },
        event_id: '1-0',
      }),
    )
    await flushMicrotasks()
    expect(useMessageStore.getState().streamingConversationId).toBe(CONV_A)

    void useMessageStore.getState().send(mockClient as never, CONV_B, 'from b')
    await flushMicrotasks(10)
    // Close controllable bodies so send() loops can exit (jsdom abort is flaky).
    a.close()
    await flushMicrotasks(10)

    expect(useMessageStore.getState().isStreaming).toBe(true)
    expect(useMessageStore.getState().streamingConversationId).toBe(CONV_B)

    b.close()
    await flushMicrotasks(5)
  })
})
