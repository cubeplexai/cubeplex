import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { FailoverEvent } from '../../src/types/events'

// The SSE branch that appends a model_failover event lives inside
// consumeRunStream / send. Both are async streaming functions that cannot
// be driven from a plain unit test. We assert the PUBLIC ACTION CONTRACT
// (`appendFailoverEvent`) instead — that's what the SSE consumer calls,
// and it covers the conversation-scoped append + ordering guarantees the
// MessageList renderer depends on.

const CONV = 'conv-failover-A'
const OTHER = 'conv-failover-B'

function makeEvent(overrides: Partial<FailoverEvent['data']> = {}): FailoverEvent {
  return {
    type: 'model_failover',
    timestamp: '2026-06-10T12:00:00Z',
    agent_id: null,
    agent_name: null,
    data: {
      failed_ref: 'anthropic/claude-3-5-sonnet',
      next_ref: 'openai/gpt-4o',
      reason: 'rate_limit_exceeded',
      ...overrides,
    },
  } as FailoverEvent
}

beforeEach(() => {
  useMessageStore.setState({ failoverEvents: {} })
})

describe('messageStore — failoverEvents slice', () => {
  it('appends events under the conversation key', () => {
    useMessageStore.getState().appendFailoverEvent(CONV, makeEvent())
    const list = useMessageStore.getState().failoverEvents[CONV]
    expect(list).toHaveLength(1)
    expect(list[0].data.failed_ref).toBe('anthropic/claude-3-5-sonnet')
  })

  it('preserves insertion order across multiple events', () => {
    useMessageStore
      .getState()
      .appendFailoverEvent(CONV, makeEvent({ failed_ref: 'a/x', next_ref: 'a/y' }))
    useMessageStore
      .getState()
      .appendFailoverEvent(
        CONV,
        makeEvent({ failed_ref: 'a/y', next_ref: null, reason: 'chain_exhausted' }),
      )
    const list = useMessageStore.getState().failoverEvents[CONV]
    expect(list.map((e) => e.data.failed_ref)).toEqual(['a/x', 'a/y'])
    // Chain-exhausted case: `next_ref` stays explicitly null. The renderer
    // must NOT show the literal "null" — that's covered in the banner test.
    expect(list[1].data.next_ref).toBeNull()
  })

  it('keeps per-conversation lists isolated', () => {
    useMessageStore.getState().appendFailoverEvent(CONV, makeEvent({ failed_ref: 'A/1' }))
    useMessageStore.getState().appendFailoverEvent(OTHER, makeEvent({ failed_ref: 'B/1' }))
    const state = useMessageStore.getState().failoverEvents
    expect(state[CONV]).toHaveLength(1)
    expect(state[CONV][0].data.failed_ref).toBe('A/1')
    expect(state[OTHER]).toHaveLength(1)
    expect(state[OTHER][0].data.failed_ref).toBe('B/1')
  })

  it('returns undefined when no events have been recorded for a conversation', () => {
    expect(useMessageStore.getState().failoverEvents['unknown-conv']).toBeUndefined()
  })
})
