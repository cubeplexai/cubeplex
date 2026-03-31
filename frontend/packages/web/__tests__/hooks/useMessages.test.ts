import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act } from '@testing-library/react'
import { useMessageStore } from '@cubebox/core/stores'

function mockSSEResponse(events: object[]) {
  const lines = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(lines))
      controller.close()
    },
  })
  return new Response(stream, {
    headers: { 'content-type': 'text/event-stream' },
  })
}

const mockClient = { baseUrl: '', get: vi.fn(), post: vi.fn() }

beforeEach(() => {
  useMessageStore.setState({
    messages: [],
    streamAgents: {},
    isStreaming: false,
    error: null,
  })
})

describe('messageStore.send', () => {
  it('adds user message optimistically', async () => {
    vi.stubGlobal('fetch', vi.fn(() => mockSSEResponse([
      { type: 'done', data: {}, agent_id: null, agent_name: null, timestamp: '' }
    ])))

    await act(async () => {
      await useMessageStore.getState().send(mockClient as any, 'conv-1', 'hello')
    })

    const { messages } = useMessageStore.getState()
    expect(messages.some((m) => m.role === 'user' && m.content === 'hello')).toBe(true)
  })

  it('accumulates text_delta events into streamAgents', async () => {
    vi.stubGlobal('fetch', vi.fn(() => mockSSEResponse([
      { type: 'text_delta', data: { content: 'Hello' }, agent_id: null, agent_name: null, timestamp: '' },
      { type: 'text_delta', data: { content: ' world' }, agent_id: null, agent_name: null, timestamp: '' },
      { type: 'done', data: {}, agent_id: null, agent_name: null, timestamp: '' },
    ])))

    await act(async () => {
      await useMessageStore.getState().send(mockClient as any, 'conv-1', 'hi')
    })

    const { messages } = useMessageStore.getState()
    const assistantMsg = messages.find((m) => m.role === 'assistant')
    expect(assistantMsg?.content).toBe('Hello world')
  })

  it('sets error on error event', async () => {
    vi.stubGlobal('fetch', vi.fn(() => mockSSEResponse([
      { type: 'error', data: { error_code: 'ERR', message: 'Something failed' }, agent_id: null, agent_name: null, timestamp: '' },
    ])))

    await act(async () => {
      await useMessageStore.getState().send(mockClient as any, 'conv-1', 'hi')
    })

    expect(useMessageStore.getState().error).toBe('Something failed')
  })

  it('clears isStreaming after completion', async () => {
    vi.stubGlobal('fetch', vi.fn(() => mockSSEResponse([
      { type: 'done', data: {}, agent_id: null, agent_name: null, timestamp: '' },
    ])))

    await act(async () => {
      await useMessageStore.getState().send(mockClient as any, 'conv-1', 'hi')
    })

    expect(useMessageStore.getState().isStreaming).toBe(false)
  })
})
