import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { AgentEvent } from '../../src/types'

function makeRequestEvent(overrides?: {
  question_id?: string
  tool_call_id?: string
  command?: string
  matched_pattern?: string | null
  timeout_seconds?: number | null
}): AgentEvent {
  return {
    type: 'sandbox_confirm_request',
    event_id: '1000-1',
    timestamp: new Date().toISOString(),
    agent_id: null,
    agent_name: null,
    data: {
      question_id: overrides?.question_id ?? 'qid-1',
      tool_call_id: overrides?.tool_call_id ?? 'tc-1',
      command: overrides?.command ?? 'rm -rf /tmp/x',
      matched_pattern: overrides?.matched_pattern ?? 'rm *',
      timeout_seconds: overrides?.timeout_seconds ?? 180,
    },
  } as unknown as AgentEvent
}

function makeResolvedEvent(questionId = 'qid-1'): AgentEvent {
  return {
    type: 'sandbox_confirm_resolved',
    event_id: '1000-2',
    timestamp: new Date().toISOString(),
    agent_id: null,
    agent_name: null,
    data: {
      question_id: questionId,
      decision: 'approve',
      cancelled: false,
      timed_out: false,
      reason: null,
    },
  } as unknown as AgentEvent
}

beforeEach(() => {
  useMessageStore.setState({ pendingConfirmMap: {}, lastAppliedEventId: null })
})

describe('sandbox_confirm_request', () => {
  it('adds entry to pendingConfirmMap keyed by tool_call_id', () => {
    useMessageStore.getState().__applyEvent(makeRequestEvent())
    const map = useMessageStore.getState().pendingConfirmMap
    expect(map['tc-1']).toEqual({
      question_id: 'qid-1',
      command: 'rm -rf /tmp/x',
      matched_pattern: 'rm *',
      timeout_seconds: 180,
      requestedAt: expect.any(Number),
      // Live SSE without an active currentRunId falls back to '' — the
      // store carries run_id so the resume answer-submit URL can be built.
      run_id: '',
    })
  })

  it('is idempotent — duplicate event_id is ignored', () => {
    const evt = makeRequestEvent()
    useMessageStore.getState().__applyEvent(evt)
    useMessageStore.getState().__applyEvent(evt)
    expect(Object.keys(useMessageStore.getState().pendingConfirmMap)).toHaveLength(1)
  })
})

describe('sandbox_confirm_resolved', () => {
  it('removes entry from pendingConfirmMap by question_id', () => {
    useMessageStore.getState().__applyEvent(makeRequestEvent())
    useMessageStore.getState().__applyEvent(makeResolvedEvent('qid-1'))
    expect(useMessageStore.getState().pendingConfirmMap['tc-1']).toBeUndefined()
  })

  it('is a no-op when question_id is not pending', () => {
    useMessageStore.getState().__applyEvent(makeResolvedEvent('qid-unknown'))
    expect(useMessageStore.getState().pendingConfirmMap).toEqual({})
  })
})
