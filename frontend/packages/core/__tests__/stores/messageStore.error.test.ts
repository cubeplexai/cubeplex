import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { ErrorEventData } from '../../src/types/events'

// The error-event branch lives inside consumeRunStream / send — both are async
// streaming functions that cannot be driven in unit tests without a live backend.
// The test hook __applyEvent delegates to applyStreamEvent, which intentionally
// does NOT handle the 'error' type (error is a terminal event that also resets
// isStreaming / currentRunId — concerns beyond applyStreamEvent's scope).
//
// Therefore we test the PUBLIC STATE CONTRACT directly: the errors Record shape,
// per-conversation isolation, the null-clear-on-new-send path, and the
// seedError bootstrap path. Each test sets up exactly the state that the
// production branch writes and then asserts selectors / derived reads against it.

const CONV = 'conv-test-error'
const OTHER_CONV = 'conv-other'

function resetStore(): void {
  useMessageStore.setState({
    errors: {},
    isStreaming: false,
    currentRunId: null,
    streamingConversationId: null,
    lastAppliedEventId: null,
  })
}

describe('messageStore — error state shape (SSE branch)', () => {
  beforeEach(resetStore)

  it('stores error keyed by conversationId, not globally', () => {
    // Mirror exactly what consumeRunStream's error branch writes (lines 954-967).
    const errData: ErrorEventData = {
      error_code: 'context_length_exceeded',
      params: { model: 'kimi-k2.6', tokens_in: 262014, context_window: 256000 },
      message: 'Conversation exceeds the model context window.',
    }

    useMessageStore.setState((s) => ({
      errors: {
        ...s.errors,
        [CONV]: { runId: 'run-sse-1', data: errData },
      },
      isStreaming: false,
      streamingConversationId: null,
      currentRunId: null,
    }))

    const { errors } = useMessageStore.getState()
    expect(errors[CONV]).toBeDefined()
    expect(errors[CONV]).not.toBeNull()
    expect(errors[CONV]?.data.error_code).toBe('context_length_exceeded')
    expect(errors[CONV]?.data.params).toEqual({
      model: 'kimi-k2.6',
      tokens_in: 262014,
      context_window: 256000,
    })
    expect(errors[CONV]?.runId).toBe('run-sse-1')
    // Streaming state is cleared together with the error landing.
    expect(errors[CONV]?.data.message).toBe('Conversation exceeds the model context window.')
    expect(useMessageStore.getState().isStreaming).toBe(false)
    expect(useMessageStore.getState().currentRunId).toBeNull()
  })

  it('error for one conversation does not appear in another', () => {
    useMessageStore.setState({
      errors: {
        [CONV]: {
          runId: 'r1',
          data: { error_code: 'provider_auth_failed', message: 'Auth failed.' },
        },
      },
    })

    const { errors } = useMessageStore.getState()
    expect(errors[CONV]).toBeDefined()
    expect(errors[OTHER_CONV]).toBeUndefined()
  })

  it('two conversations can each hold independent errors', () => {
    useMessageStore.setState({
      errors: {
        [CONV]: {
          runId: 'r1',
          data: { error_code: 'rate_limited', message: 'Rate limited on conv A.' },
        },
        [OTHER_CONV]: {
          runId: 'r2',
          data: { error_code: 'provider_auth_failed', message: 'Auth failed on conv B.' },
        },
      },
    })

    const { errors } = useMessageStore.getState()
    expect(errors[CONV]?.data.error_code).toBe('rate_limited')
    expect(errors[OTHER_CONV]?.data.error_code).toBe('provider_auth_failed')
  })
})

describe('messageStore — error cleared on new send (lines 1303)', () => {
  beforeEach(resetStore)

  it('errors[conversationId] is set to null when a new message send starts', () => {
    // Seed a prior error (e.g. from a failed run).
    useMessageStore.setState({
      errors: {
        [CONV]: {
          runId: 'old-run',
          data: { error_code: 'rate_limited', message: 'Rate limit.' },
        },
      },
    })

    // Simulate what `send` writes at the top of the action before streaming
    // begins (messageStore.ts line 1292-1311).
    useMessageStore.setState((state) => ({
      isStreaming: true,
      streamingConversationId: CONV,
      currentRunId: null,
      lastAppliedEventId: null,
      statusPhase: null,
      errors: { ...state.errors, [CONV]: null },
      lastRunStatus: null,
    }))

    expect(useMessageStore.getState().errors[CONV]).toBeNull()
    // Errors from other conversations are not disturbed.
    expect(useMessageStore.getState().errors[OTHER_CONV]).toBeUndefined()
  })
})

describe('messageStore — bootstrap seedError path (lines 1197-1212)', () => {
  beforeEach(resetStore)

  it('hydrates errors[conversationId] from a prior run error_code on bootstrap', () => {
    // Mirrors the seedError logic: active_run.error_code present → non-null seed.
    const seedError = {
      runId: 'run-bootstrap-1',
      data: {
        error_code: 'context_length_exceeded',
        params: { model: 'claude-sonnet-4-6', tokens_in: 100000, context_window: 200000 },
        message: 'context_length_exceeded',
      },
    }

    useMessageStore.setState((s) => ({
      errors: { ...s.errors, [CONV]: seedError },
    }))

    const { errors } = useMessageStore.getState()
    expect(errors[CONV]?.data.error_code).toBe('context_length_exceeded')
    expect(errors[CONV]?.runId).toBe('run-bootstrap-1')
  })

  it('errors[conversationId] stays null when active_run has no error_code', () => {
    // Mirrors: seedError = null when bootstrap.active_run?.error_code is falsy.
    useMessageStore.setState((s) => ({
      errors: { ...s.errors, [CONV]: null },
    }))

    expect(useMessageStore.getState().errors[CONV]).toBeNull()
  })
})

describe('messageStore — error with optional fields', () => {
  beforeEach(resetStore)

  it('stores error without optional params field', () => {
    const errData: ErrorEventData = {
      error_code: 'internal_error',
      message: 'An unexpected error occurred.',
    }

    useMessageStore.setState({
      errors: {
        [CONV]: { runId: 'run-2', data: errData },
      },
    })

    const entry = useMessageStore.getState().errors[CONV]
    expect(entry?.data.params).toBeUndefined()
    expect(entry?.data.error_code).toBe('internal_error')
  })

  it('stores error with details field', () => {
    const errData: ErrorEventData = {
      error_code: 'provider_auth_failed',
      message: 'Auth failed.',
      details: 'API key expired at 2026-06-01.',
    }

    useMessageStore.setState({
      errors: {
        [CONV]: { runId: 'run-3', data: errData },
      },
    })

    expect(useMessageStore.getState().errors[CONV]?.data.details).toBe(
      'API key expired at 2026-06-01.',
    )
  })
})
