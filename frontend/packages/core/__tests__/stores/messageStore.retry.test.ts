import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { RetryEvent } from '../../src/types/events'

const CONV = 'conv-retry-A'
const OTHER = 'conv-retry-B'

function makeEvent(overrides: Partial<RetryEvent['data']> = {}): RetryEvent {
  return {
    type: 'model_retry',
    timestamp: '2026-08-16T12:00:00Z',
    agent_id: null,
    agent_name: null,
    data: {
      model_ref: 'ark/glm-5.2',
      reason: 'simulated 429',
      attempt: 1,
      wait_s: 2,
      ...overrides,
    },
  } as RetryEvent
}

beforeEach(() => {
  useMessageStore.setState({ retryEvents: {} })
})

describe('messageStore — retryEvents slice', () => {
  it('stores the latest retry per conversation', () => {
    useMessageStore.getState().setRetryEvent(CONV, makeEvent())
    useMessageStore.getState().setRetryEvent(CONV, makeEvent({ attempt: 2, wait_s: 5 }))
    const event = useMessageStore.getState().retryEvents[CONV]
    expect(event?.data.attempt).toBe(2)
    expect(event?.data.wait_s).toBe(5)
  })

  it('keeps per-conversation retries isolated', () => {
    useMessageStore.getState().setRetryEvent(CONV, makeEvent({ model_ref: 'a/m1' }))
    useMessageStore.getState().setRetryEvent(OTHER, makeEvent({ model_ref: 'b/m2' }))
    const state = useMessageStore.getState().retryEvents
    expect(state[CONV]?.data.model_ref).toBe('a/m1')
    expect(state[OTHER]?.data.model_ref).toBe('b/m2')
  })

  it('clearRetryEvent drops only that conversation', () => {
    useMessageStore.getState().setRetryEvent(CONV, makeEvent())
    useMessageStore.getState().setRetryEvent(OTHER, makeEvent({ model_ref: 'b/m2' }))
    useMessageStore.getState().clearRetryEvent(CONV)
    expect(useMessageStore.getState().retryEvents[CONV]).toBeNull()
    expect(useMessageStore.getState().retryEvents[OTHER]?.data.model_ref).toBe('b/m2')
  })
})
