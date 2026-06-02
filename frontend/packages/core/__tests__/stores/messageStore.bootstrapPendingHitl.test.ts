import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { ApiClient } from '../../src/api'

// Helper: construct a stub ApiClient whose ``get`` returns a Response-like
// object containing the given bootstrap body. The store only touches
// ``client.get(...)`` for ``loadMessages`` (and skips the active_run branch
// when ``active_run: null``), so we can leave the rest of the surface bare.
function makeBootstrapClient(body: Record<string, unknown>): ApiClient {
  const response = {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  }
  return {
    get: vi.fn().mockResolvedValue(response),
  } as unknown as ApiClient
}

beforeEach(() => {
  useMessageStore.setState({
    pendingAsk: null,
    pendingConfirmMap: {},
    isStreaming: false,
    streamingConversationId: null,
    currentRunId: null,
    error: null,
  })
})

describe('loadMessages → bootstrap.pending_hitl seeds pending state', () => {
  it('seeds pendingAsk (singular) when bootstrap returns an ask_user request', async () => {
    const client = makeBootstrapClient({
      messages: [],
      active_run: null,
      total: 0,
      last_run_status: null,
      pending_hitl: {
        run_id: 'run-ask-1',
        question_id: 'qid-ask-1',
        kind: 'ask_user',
        requested_at: '2026-06-02T00:00:00.000Z',
        questions: [
          {
            key: 'color',
            prompt: 'Pick a color',
            options: [{ label: 'Red', value: 'red', description: null, allow_input: false }],
            multi_select: false,
            required: true,
          },
        ],
      },
    })

    await useMessageStore.getState().loadMessages(client, 'conv-1')

    const ask = useMessageStore.getState().pendingAsk
    expect(ask).not.toBeNull()
    expect(ask?.question_id).toBe('qid-ask-1')
    expect(ask?.run_id).toBe('run-ask-1')
    expect(ask?.questions).toHaveLength(1)
    expect(ask?.questions[0].key).toBe('color')
    // requested_at parsed into epoch ms
    expect(ask?.requestedAt).toBe(Date.parse('2026-06-02T00:00:00.000Z'))
    // sandbox map stays empty
    expect(Object.keys(useMessageStore.getState().pendingConfirmMap)).toHaveLength(0)
  })

  it('seeds pendingConfirmMap keyed by tool_call_id for a sandbox_confirm request', async () => {
    const client = makeBootstrapClient({
      messages: [],
      active_run: null,
      total: 0,
      last_run_status: null,
      pending_hitl: {
        run_id: 'run-cf-1',
        question_id: 'qid-cf-1',
        kind: 'sandbox_confirm',
        requested_at: '2026-06-02T00:00:00.000Z',
        tool_call_id: 'tc-1',
        command: 'rm -rf /tmp/x',
        matched_pattern: 'rm *',
      },
    })

    await useMessageStore.getState().loadMessages(client, 'conv-1')

    const map = useMessageStore.getState().pendingConfirmMap
    const entry = map['tc-1']
    expect(entry).toBeTruthy()
    expect(entry.question_id).toBe('qid-cf-1')
    expect(entry.command).toBe('rm -rf /tmp/x')
    expect(entry.matched_pattern).toBe('rm *')
    expect(entry.run_id).toBe('run-cf-1')
    expect(entry.requestedAt).toBe(Date.parse('2026-06-02T00:00:00.000Z'))
    // ask stays null
    expect(useMessageStore.getState().pendingAsk).toBeNull()
  })

  it('falls back to Date.now() when requested_at is malformed', async () => {
    const before = Date.now()
    const client = makeBootstrapClient({
      messages: [],
      active_run: null,
      total: 0,
      last_run_status: null,
      pending_hitl: {
        run_id: 'run-ask-2',
        question_id: 'qid-ask-2',
        kind: 'ask_user',
        requested_at: 'not-a-date',
        questions: [],
      },
    })

    await useMessageStore.getState().loadMessages(client, 'conv-2')

    const ask = useMessageStore.getState().pendingAsk
    expect(ask).not.toBeNull()
    expect(ask?.requestedAt).toBeGreaterThanOrEqual(before)
  })

  it('leaves pendingAsk null + pendingConfirmMap empty when pending_hitl is null', async () => {
    const client = makeBootstrapClient({
      messages: [],
      active_run: null,
      total: 0,
      last_run_status: null,
      pending_hitl: null,
    })

    await useMessageStore.getState().loadMessages(client, 'conv-3')

    expect(useMessageStore.getState().pendingAsk).toBeNull()
    expect(Object.keys(useMessageStore.getState().pendingConfirmMap)).toHaveLength(0)
  })

  it('leaves pendingAsk null + pendingConfirmMap empty when pending_hitl is absent', async () => {
    const client = makeBootstrapClient({
      messages: [],
      active_run: null,
      total: 0,
      last_run_status: null,
    })

    await useMessageStore.getState().loadMessages(client, 'conv-4')

    expect(useMessageStore.getState().pendingAsk).toBeNull()
    expect(Object.keys(useMessageStore.getState().pendingConfirmMap)).toHaveLength(0)
  })
})
