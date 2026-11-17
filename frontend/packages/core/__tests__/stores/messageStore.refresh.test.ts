import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApiClient } from '../../src/api'
import { useMessageStore } from '../../src/stores/messageStore'

const conversationId = 'conv-refresh'
const refreshOptions = { conditional: true, preserveLoadedHistory: true }
const payload = {
  messages: [
    {
      id: 'message-1',
      seq: 1,
      role: 'assistant',
      content: [{ type: 'text', text: 'Existing answer' }],
    },
  ],
  active_run: null,
  pending_hitl: null,
  pending_steers: [],
  last_run_status: null,
  oldest_seq: 1,
  has_more: false,
}

function response(etag: string, extra: Record<string, unknown> = {}): Response {
  return Response.json({ ...payload, ...extra }, { headers: { ETag: etag } })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

describe('conversation baseline refresh', () => {
  const fetchMock = vi.fn<typeof fetch>()
  const client = createApiClient('')

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    client.setWorkspaceId('ws-refresh')
    useMessageStore.setState(useMessageStore.getInitialState(), true)
  })

  afterEach(() => vi.unstubAllGlobals())

  it('does not publish any store update for an unchanged baseline', async () => {
    fetchMock.mockResolvedValueOnce(response('W/"one"'))
    await useMessageStore.getState().loadMessages(client, conversationId)
    const before = useMessageStore.getState()
    const changed = vi.fn()
    const unsubscribe = useMessageStore.subscribe(changed)
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 304 }))

    await before.loadMessages(client, conversationId, refreshOptions)

    unsubscribe()
    expect(fetchMock).toHaveBeenLastCalledWith(
      `/api/v1/ws/ws-refresh/conversations/${conversationId}/bootstrap`,
      expect.objectContaining({
        cache: 'no-store',
        headers: expect.objectContaining({ 'If-None-Match': 'W/"one"' }),
      }),
    )
    expect(changed).not.toHaveBeenCalled()
    expect(useMessageStore.getState()).toBe(before)
  })

  it('collapses a local compaction marker into the persisted row', async () => {
    useMessageStore.setState({
      messages: {
        [conversationId]: [
          {
            id: 'local-marker',
            role: 'user',
            content: [{ type: 'text', text: '' }],
            metadata: {
              synthetic: true,
              synthetic_source: 'compaction',
              kind: 'compaction',
              compaction: { source: 'manual', boundary: 8 },
            },
          },
        ],
      },
    })
    fetchMock.mockResolvedValueOnce(
      response('W/"compact"', {
        messages: [
          {
            id: '',
            seq: 4,
            role: 'user',
            content: [{ type: 'text', text: '' }],
            metadata: {
              synthetic: true,
              synthetic_source: 'compaction',
              kind: 'compaction',
              compaction: { source: 'manual', boundary: 8 },
            },
          },
        ],
        oldest_seq: 4,
      }),
    )

    await useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)

    const markers = (useMessageStore.getState().messages[conversationId] ?? []).filter(
      (message) => message.metadata?.synthetic_source === 'compaction',
    )
    expect(markers).toHaveLength(1)
    expect(markers[0]?.seq).toBe(4)
  })

  it('updates control state without replacing unchanged history or card props', async () => {
    fetchMock.mockResolvedValueOnce(response('W/"one"'))
    await useMessageStore.getState().loadMessages(client, conversationId)
    const before = useMessageStore.getState()
    fetchMock.mockResolvedValueOnce(response('W/"two"', { execution_generation: 2 }))

    await before.loadMessages(client, conversationId, refreshOptions)

    const after = useMessageStore.getState()
    expect(after.executionGeneration[conversationId]).toBe(2)
    expect(after.messages[conversationId]).toBe(before.messages[conversationId])
    expect(after.pendingConfirmMap).toBe(before.pendingConfirmMap)
    expect(after.toolResultMap).toBe(before.toolResultMap)
    expect(after.backgroundEvents[conversationId]).toBe(before.backgroundEvents[conversationId])
  })

  it('keeps unchanged message identities when another message changes', async () => {
    fetchMock.mockResolvedValueOnce(response('W/"one"'))
    await useMessageStore.getState().loadMessages(client, conversationId)
    const existing = useMessageStore.getState().messages[conversationId][0]
    fetchMock.mockResolvedValueOnce(
      response('W/"two"', {
        messages: [
          ...payload.messages,
          {
            ...payload.messages[0],
            id: 'message-2',
            seq: 2,
            content: [{ type: 'text', text: 'New answer' }],
          },
        ],
      }),
    )

    await useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)

    expect(useMessageStore.getState().messages[conversationId][0]).toBe(existing)
    expect(useMessageStore.getState().messages[conversationId][1].id).toBe('message-2')
  })

  it('keeps persisted messages without IDs mounted across bootstrap responses', async () => {
    const messages = [{ seq: 1, role: 'assistant', content: [{ type: 'text', text: 'Saved' }] }]
    fetchMock.mockResolvedValueOnce(response('W/"one"', { messages }))
    await useMessageStore.getState().loadMessages(client, conversationId)
    const original = useMessageStore.getState().messages[conversationId][0]
    fetchMock.mockResolvedValueOnce(response('W/"two"', { messages, execution_generation: 2 }))

    await useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)

    const updated = useMessageStore.getState().messages[conversationId][0]
    expect(updated.id).toBe(original.id)
    expect(updated).toBe(original)
  })

  it('does not retain a validator from a response discarded after streaming starts', async () => {
    fetchMock.mockResolvedValueOnce(response('W/"one"'))
    await useMessageStore.getState().loadMessages(client, conversationId)
    const request = deferred<Response>()
    fetchMock.mockReturnValueOnce(request.promise)
    const refresh = useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)
    useMessageStore.setState({ isStreaming: true, streamingConversationId: conversationId })
    request.resolve(response('W/"discarded"'))
    await refresh
    useMessageStore.setState({ isStreaming: false, streamingConversationId: null })
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 304 }))

    await useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)

    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({ 'If-None-Match': 'W/"one"' }),
      }),
    )
  })

  it('runs an unconditional recovery after an in-flight conditional refresh', async () => {
    fetchMock.mockResolvedValueOnce(response('W/"one"'))
    await useMessageStore.getState().loadMessages(client, conversationId)
    const request = deferred<Response>()
    fetchMock.mockReturnValueOnce(request.promise)
    const refresh = useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)
    fetchMock.mockResolvedValueOnce(response('W/"two"'))
    const recovery = useMessageStore.getState().loadMessages(client, conversationId)
    const secondRecovery = useMessageStore.getState().loadMessages(client, conversationId)
    request.resolve(new Response(null, { status: 304 }))

    await Promise.all([refresh, recovery, secondRecovery])

    expect(fetchMock).toHaveBeenCalledTimes(3)
    const init = fetchMock.mock.calls[2][1]
    expect(init?.headers).not.toHaveProperty('If-None-Match')
  })

  it('does not reuse a validator after changing workspace or discarding local history', async () => {
    fetchMock.mockResolvedValueOnce(response('W/"one"'))
    await useMessageStore.getState().loadMessages(client, conversationId)
    client.setWorkspaceId('ws-other')
    fetchMock.mockResolvedValueOnce(response('W/"other"'))
    await useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)
    expect(fetchMock.mock.calls[1][1]?.headers).not.toHaveProperty('If-None-Match')

    useMessageStore.setState({ messages: {} })
    fetchMock.mockResolvedValueOnce(response('W/"other"'))
    await useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)
    expect(fetchMock.mock.calls[2][1]?.headers).not.toHaveProperty('If-None-Match')
    expect(useMessageStore.getState().messages[conversationId][0].id).toBe('message-1')
  })

  it('replaces a live background result card with its checkpoint row', async () => {
    fetchMock.mockResolvedValueOnce(response('W/"one"'))
    await useMessageStore.getState().loadMessages(client, conversationId)
    useMessageStore.setState({
      messages: {
        [conversationId]: [
          ...useMessageStore.getState().messages[conversationId],
          {
            id: 'local-notice',
            role: 'user',
            content: [{ type: 'text', text: 'Background task result:\n{"task_id":"task-1"}' }],
            metadata: {
              source: 'background_task',
              notice_id: 'bge-1',
              steer_id: 'bge-1',
            },
          },
        ],
      },
    })
    fetchMock.mockResolvedValueOnce(
      response('W/"two"', {
        messages: [
          ...payload.messages,
          {
            id: 'history-notice',
            seq: 2,
            role: 'user',
            content: [
              {
                type: 'text',
                text: 'Background task result:\n{"task_id":"task-1","summary":"done"}',
              },
            ],
            metadata: { source: 'background_task', notice_id: 'bge-1' },
          },
        ],
      }),
    )

    await useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)

    const ids = useMessageStore.getState().messages[conversationId].map((message) => message.id)
    expect(ids).toEqual(['message-1', 'history-notice'])
  })

  it('replaces pre-steer assistant/tool turns instead of sorting duplicates to the bottom', async () => {
    fetchMock.mockResolvedValueOnce(response('W/"one"'))
    await useMessageStore.getState().loadMessages(client, conversationId)

    // Simulate a live run that emits text + a tool call (with a running update
    // followed by completion), drains a steer via __commitTurnAndInject, then
    // emits the final answer and completes.
    useMessageStore.setState({
      isStreaming: true,
      streamingConversationId: conversationId,
      currentRunId: 'run-steered',
      streamAgents: {},
      toolStartedMap: {},
      toolResultMap: {},
      turnUsage: { [conversationId]: null },
    })
    const apply = useMessageStore.getState().__applyEvent
    apply({
      type: 'text_delta',
      timestamp: '2026-11-17T12:14:00.000Z',
      data: { content: 'Preparing file before secret arrives.' },
      agent_id: null,
      agent_name: null,
    })
    apply({
      type: 'tool_call',
      timestamp: '2026-11-17T12:14:08.625Z',
      data: {
        tool_call_id: 'call_write_1',
        name: 'write',
        arguments: { file_path: '/workspace/tmp/report_stage4.md', content: '# Report' },
      },
      agent_id: null,
      agent_name: null,
    })
    apply({
      type: 'tool_result',
      timestamp: '2026-11-17T12:14:08.700Z',
      data: {
        tool_call_id: 'call_write_1',
        name: 'write',
        content: 'writing...',
        details: { status: 'running' },
      } as never,
      agent_id: null,
      agent_name: null,
    })
    apply({
      type: 'tool_result',
      timestamp: '2026-11-17T12:14:08.931Z',
      data: {
        tool_call_id: 'call_write_1',
        name: 'write',
        content: 'Successfully wrote /workspace/tmp/report_stage4.md',
        details: {
          status: 'exited',
          tool_started_at: '2026-11-17T12:14:08.702Z',
          tool_ended_at: '2026-11-17T12:14:08.929Z',
        },
      } as never,
      agent_id: null,
      agent_name: null,
    })

    useMessageStore.getState().__commitTurnAndInject(conversationId, {
      content: 'secret-value',
      steer_id: 'steer-1',
    })

    // Post-steer final answer finalized at the end of the run.
    useMessageStore.setState((s) => ({
      messages: {
        ...s.messages,
        [conversationId]: [
          ...(s.messages[conversationId] ?? []),
          {
            id: 'local-final-assistant',
            role: 'assistant',
            run_id: 'run-steered',
            timestamp: 1794918005,
            content: [{ type: 'text', text: 'Sent and verified.' }],
          },
        ],
      },
      streamAgents: {},
      isStreaming: false,
      streamingConversationId: null,
      currentRunId: null,
    }))

    // Baseline refresh returns persisted rows; cubeloop steer UserMessages have
    // timestamp: null, which previously pushed unsequenced orphans to the tail.
    fetchMock.mockResolvedValueOnce(
      response('W/"two"', {
        messages: [
          ...payload.messages,
          {
            id: 'persisted-pre-assistant',
            seq: 2,
            role: 'assistant',
            run_id: 'run-steered',
            timestamp: 1794917545,
            content: [
              { type: 'text', text: 'Preparing file before secret arrives.' },
              {
                type: 'tool_call',
                id: 'call_write_1',
                name: 'write',
                arguments: { file_path: '/workspace/tmp/report_stage4.md', content: '# Report' },
              },
            ],
          },
          {
            id: 'persisted-pre-tool',
            seq: 3,
            role: 'tool_result',
            run_id: 'run-steered',
            tool_call_id: 'call_write_1',
            tool_name: 'write',
            timestamp: 1794917648,
            content: [{ type: 'text', text: 'Successfully wrote /workspace/tmp/report_stage4.md' }],
            details: {
              status: 'exited',
              tool_started_at: '2026-11-17T12:14:08.702Z',
              tool_ended_at: '2026-11-17T12:14:08.929Z',
            },
          },
          {
            id: 'persisted-steer',
            seq: 4,
            role: 'user',
            run_id: 'run-steered',
            timestamp: null,
            content: [{ type: 'text', text: 'secret-value' }],
            metadata: { steer_id: 'steer-1' },
          },
          {
            id: 'persisted-final-assistant',
            seq: 5,
            role: 'assistant',
            run_id: 'run-steered',
            timestamp: 1794917978,
            content: [{ type: 'text', text: 'Sent and verified.' }],
          },
        ],
      }),
    )

    await useMessageStore.getState().loadMessages(client, conversationId, refreshOptions)

    const after = useMessageStore.getState()
    expect(after.messages[conversationId].map((m) => m.id)).toEqual([
      'message-1',
      'persisted-pre-assistant',
      'persisted-pre-tool',
      'persisted-steer',
      'persisted-final-assistant',
    ])
    expect(after.toolResultMap.call_write_1?.details).toMatchObject({ status: 'exited' })
  })
})
