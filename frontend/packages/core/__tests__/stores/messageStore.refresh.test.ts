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
})
