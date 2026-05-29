import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { AgentEvent } from '../../src/types'

const TS = new Date().toISOString()

function makeRequestEvent(overrides?: {
  question_id?: string
  timeout_seconds?: number | null
}): AgentEvent {
  return {
    type: 'ask_user_request',
    event_id: 'ev-1',
    timestamp: TS,
    agent_id: null,
    agent_name: null,
    data: {
      question_id: overrides?.question_id ?? 'qid-ask-1',
      questions: [
        {
          key: 'color',
          prompt: 'Pick a color?',
          options: null,
          multi_select: false,
          required: true,
        },
      ],
      timeout_seconds: overrides?.timeout_seconds ?? 120,
    },
  } as unknown as AgentEvent
}

function makeResolvedEvent(questionId = 'qid-ask-1'): AgentEvent {
  return {
    type: 'ask_user_resolved',
    event_id: 'ev-2',
    timestamp: TS,
    agent_id: null,
    agent_name: null,
    data: {
      question_id: questionId,
      answers: { color: 'red' },
      cancelled: false,
      timed_out: false,
    },
  } as unknown as AgentEvent
}

beforeEach(() => {
  useMessageStore.setState({ pendingAsk: null, lastAppliedEventId: null })
})

describe('ask_user_request', () => {
  it('sets pendingAsk with question data and requestedAt', () => {
    useMessageStore.getState().__applyEvent(makeRequestEvent())
    const ask = useMessageStore.getState().pendingAsk
    expect(ask).not.toBeNull()
    expect(ask?.question_id).toBe('qid-ask-1')
    expect(ask?.questions).toHaveLength(1)
    expect(ask?.questions[0].key).toBe('color')
    expect(ask?.timeout_seconds).toBe(120)
    expect(ask?.requestedAt).toBeGreaterThan(0)
  })

  it('uses event.timestamp for requestedAt', () => {
    const past = '2026-01-01T00:00:00.000Z'
    const evt = makeRequestEvent()
    evt.timestamp = past
    useMessageStore.getState().__applyEvent(evt)
    const ask = useMessageStore.getState().pendingAsk
    expect(ask?.requestedAt).toBe(new Date(past).getTime())
  })

  it('is idempotent on duplicate event_id', () => {
    const evt = makeRequestEvent()
    useMessageStore.getState().__applyEvent(evt)
    useMessageStore.getState().__applyEvent(evt) // same event_id
    expect(useMessageStore.getState().pendingAsk?.question_id).toBe('qid-ask-1')
  })
})

describe('ask_user_resolved', () => {
  it('clears pendingAsk when question_id matches', () => {
    useMessageStore.getState().__applyEvent(makeRequestEvent())
    expect(useMessageStore.getState().pendingAsk).not.toBeNull()
    useMessageStore.getState().__applyEvent(makeResolvedEvent())
    expect(useMessageStore.getState().pendingAsk).toBeNull()
  })

  it('is a no-op when no pending ask', () => {
    useMessageStore.getState().__applyEvent(makeResolvedEvent('unknown-qid'))
    expect(useMessageStore.getState().pendingAsk).toBeNull()
  })
})

describe('tool_result clears pendingAsk for ask_user tool', () => {
  it('clears pendingAsk when tool_result name is ask_user', () => {
    useMessageStore.getState().__applyEvent(makeRequestEvent())
    expect(useMessageStore.getState().pendingAsk).not.toBeNull()
    const toolResultEvt = {
      type: 'tool_result',
      event_id: 'ev-3',
      timestamp: TS,
      agent_id: null,
      agent_name: null,
      data: {
        tool_call_id: 'tc-ask-1',
        name: 'ask_user',
        content: 'User answers: {"color": "red"}',
        is_error: false,
        details: null,
      },
    } as unknown as AgentEvent
    useMessageStore.getState().__applyEvent(toolResultEvt)
    expect(useMessageStore.getState().pendingAsk).toBeNull()
  })

  it('does not clear pendingAsk for other tool results', () => {
    useMessageStore.getState().__applyEvent(makeRequestEvent())
    const toolResultEvt = {
      type: 'tool_result',
      event_id: 'ev-4',
      timestamp: TS,
      agent_id: null,
      agent_name: null,
      data: {
        tool_call_id: 'tc-other-1',
        name: 'execute',
        content: 'done',
        is_error: false,
        details: null,
      },
    } as unknown as AgentEvent
    useMessageStore.getState().__applyEvent(toolResultEvt)
    expect(useMessageStore.getState().pendingAsk).not.toBeNull()
  })
})
