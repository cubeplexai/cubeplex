import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../src/api'
import { useMessageStore } from '../../src/stores/messageStore'

const CONVERSATION_ID = 'conv-loading'

function bootstrapResponse(): Response {
  return new Response(
    JSON.stringify({
      messages: [],
      active_run: null,
      pending_hitl: null,
      pending_steers: [],
      last_run_status: null,
      oldest_seq: null,
      has_more: false,
    }),
    { headers: { 'content-type': 'application/json' } },
  )
}

describe('messageStore history loading state', () => {
  beforeEach(() => {
    useMessageStore.setState({
      messages: {},
      loadingMessagesByConv: {},
      isStreaming: false,
      streamingConversationId: null,
    } as never)
  })

  it('tracks a conversation bootstrap until its response has been applied', async () => {
    let resolveRequest: ((response: Response) => void) | undefined
    const client = {
      get: vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveRequest = resolve
          }),
      ),
    } as unknown as ApiClient

    const load = useMessageStore.getState().loadMessages(client, CONVERSATION_ID)

    await vi.waitFor(() => expect(client.get).toHaveBeenCalledOnce())
    expect(useMessageStore.getState().loadingMessagesByConv[CONVERSATION_ID]).toBe(true)

    resolveRequest?.(bootstrapResponse())
    await load

    expect(useMessageStore.getState().loadingMessagesByConv[CONVERSATION_ID]).toBe(false)
  })

  it('clears the bootstrap state when loading fails', async () => {
    const client = {
      get: vi.fn().mockRejectedValue(new Error('network unavailable')),
    } as unknown as ApiClient

    await useMessageStore.getState().loadMessages(client, CONVERSATION_ID)

    expect(useMessageStore.getState().loadingMessagesByConv[CONVERSATION_ID]).toBe(false)
  })
})
