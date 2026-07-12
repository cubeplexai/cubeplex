import { act } from '@testing-library/react'
import { useMessageStore } from '@cubeplex/core'

const CONV_ID = 'conv-yield'

function mockSSEResponse(events: object[]) {
  const lines = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(lines))
      controller.close()
    },
  })
  return new Response(stream, { headers: { 'content-type': 'text/event-stream' } })
}

const mockClient = { baseUrl: '', get: vi.fn(), post: vi.fn(), resolvePath: (p: string) => p }

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
    todos: [],
    toolStartedMap: {},
    toolResultMap: {},
  })
})

it('yields to the event loop when processing many events', async () => {
  const events: object[] = Array.from({ length: 250 }, (_, i) => ({
    type: 'text_delta',
    data: { content: 'x' },
    agent_id: null,
    agent_name: null,
    timestamp: '',
    event_id: `${i + 1}-0`,
  }))
  events.push({ type: 'done', data: {}, agent_id: null, agent_name: null, timestamp: '' })

  vi.stubGlobal(
    'fetch',
    vi.fn(() => mockSSEResponse(events)),
  )
  // Force the setTimeout fallback path (no scheduler.yield in jsdom).
  vi.stubGlobal('scheduler', undefined)
  const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout')

  await act(async () => {
    await useMessageStore.getState().send(mockClient as never, CONV_ID, 'hi')
  })

  expect(setTimeoutSpy).toHaveBeenCalled()
})
