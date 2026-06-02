// Tests for T15: frontend handling of the synthetic policy_overridden
// resolve events emitted by the respond path (T12) when an org sandbox
// policy change during the pause leaves a dangling pending.
//
// Load-bearing behaviour: the pending entry must be removed so the
// SandboxConfirmCard / AskUserCard unmounts. The console.warn is a dev
// signal — we assert it fires (matching `messageStore.ts`) but it's not
// the user-visible exit.
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { AgentEvent } from '../../src/types'

beforeEach(() => {
  useMessageStore.setState({
    pendingAsk: null,
    pendingConfirmMap: {},
    lastAppliedEventId: null,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('sandbox_confirm_resolved decision="policy_overridden"', () => {
  it('removes the pending confirm and logs a warning', () => {
    useMessageStore.setState({
      pendingConfirmMap: {
        'tc-1': {
          question_id: 'qid-1',
          command: 'rm -rf /',
          matched_pattern: 'rm *',
          timeout_seconds: null,
          requestedAt: Date.now(),
          run_id: 'run-1',
        },
      },
    })

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    const event: AgentEvent = {
      type: 'sandbox_confirm_resolved',
      event_id: '2000-1',
      timestamp: new Date().toISOString(),
      agent_id: null,
      agent_name: null,
      data: {
        question_id: 'qid-1',
        decision: 'policy_overridden',
        cancelled: false,
        timed_out: false,
        reason: 'org sandbox policy changed during pause',
      },
    } as unknown as AgentEvent

    useMessageStore.getState().__applyEvent(event)

    expect(useMessageStore.getState().pendingConfirmMap['tc-1']).toBeUndefined()
    expect(warnSpy).toHaveBeenCalledWith(
      '[sandbox_confirm_resolved] policy_overridden — pending cleared',
      expect.objectContaining({
        question_id: 'qid-1',
        tool_call_id: 'tc-1',
        reason: 'org sandbox policy changed during pause',
      }),
    )
  })

  it('is a no-op when the question_id has no matching pending entry', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const event: AgentEvent = {
      type: 'sandbox_confirm_resolved',
      event_id: '2000-2',
      timestamp: new Date().toISOString(),
      agent_id: null,
      agent_name: null,
      data: {
        question_id: 'qid-missing',
        decision: 'policy_overridden',
        cancelled: false,
        timed_out: false,
        reason: 'policy_overridden',
      },
    } as unknown as AgentEvent

    useMessageStore.getState().__applyEvent(event)

    expect(useMessageStore.getState().pendingConfirmMap).toEqual({})
    expect(warnSpy).not.toHaveBeenCalled()
  })
})

describe('ask_user_resolved cancelled=true + reason="policy_overridden"', () => {
  it('clears pendingAsk and logs a warning', () => {
    useMessageStore.setState({
      pendingAsk: {
        question_id: 'qid-ask-1',
        questions: [],
        timeout_seconds: null,
        requestedAt: Date.now(),
        run_id: 'run-1',
      },
    })

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    const event: AgentEvent = {
      type: 'ask_user_resolved',
      event_id: '2001-1',
      timestamp: new Date().toISOString(),
      agent_id: null,
      agent_name: null,
      data: {
        question_id: 'qid-ask-1',
        answers: null,
        cancelled: true,
        timed_out: false,
        reason: 'policy_overridden',
      },
    } as unknown as AgentEvent

    useMessageStore.getState().__applyEvent(event)

    expect(useMessageStore.getState().pendingAsk).toBeNull()
    expect(warnSpy).toHaveBeenCalledWith(
      '[ask_user_resolved] policy_overridden — pending cleared',
      expect.objectContaining({
        question_id: 'qid-ask-1',
        reason: 'policy_overridden',
      }),
    )
  })

  it('does not log policy_overridden warning for an ordinary cancel', () => {
    useMessageStore.setState({
      pendingAsk: {
        question_id: 'qid-ask-2',
        questions: [],
        timeout_seconds: null,
        requestedAt: Date.now(),
        run_id: 'run-1',
      },
    })

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    const event: AgentEvent = {
      type: 'ask_user_resolved',
      event_id: '2001-2',
      timestamp: new Date().toISOString(),
      agent_id: null,
      agent_name: null,
      data: {
        question_id: 'qid-ask-2',
        answers: null,
        cancelled: true,
        timed_out: false,
      },
    } as unknown as AgentEvent

    useMessageStore.getState().__applyEvent(event)

    expect(useMessageStore.getState().pendingAsk).toBeNull()
    expect(warnSpy).not.toHaveBeenCalled()
  })
})
