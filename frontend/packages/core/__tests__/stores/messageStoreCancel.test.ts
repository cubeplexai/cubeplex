import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { AgentStream } from '../../src/stores/messageStore'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    cancelActiveRun: vi.fn().mockResolvedValue({ cancelled: true, run_id: 'r1' }),
  }
})

import { cancelActiveRun } from '../../src/api'

const fakeClient = { resolvePath: (s: string) => s, post: vi.fn() } as never

function seedStreaming(conversationId: string, stream: Partial<AgentStream>): void {
  useMessageStore.setState({
    messages: { [conversationId]: [] },
    streamAgents: {
      main: {
        text: '',
        toolCalls: [],
        toolResults: [],
        thinking: '',
        blocks: [],
        name: null,
        ...stream,
      },
    },
    isStreaming: true,
    streamingConversationId: conversationId,
    currentRunId: 'r1',
  })
}

describe('messageStore.cancelStream', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.setState({
      messages: {},
      streamAgents: {},
      isStreaming: false,
      streamingConversationId: null,
      currentRunId: null,
    })
  })

  it('finalizes partial streamed content into a cancelled assistant message', async () => {
    seedStreaming('conv1', {
      text: 'partial answer',
      blocks: [{ type: 'text', text: 'partial answer' }],
    })

    await useMessageStore.getState().cancelStream(fakeClient, 'conv1')

    const state = useMessageStore.getState()
    expect(state.isStreaming).toBe(false)
    const msgs = state.messages.conv1
    expect(msgs).toHaveLength(1)
    expect(msgs[0].role).toBe('assistant')
    expect(msgs[0].stop_reason).toBe('aborted')
    expect(msgs[0].content).toEqual([{ type: 'text', text: 'partial answer' }])
    expect(cancelActiveRun).toHaveBeenCalledOnce()
  })

  it('does not append an empty bubble when nothing was streamed', async () => {
    seedStreaming('conv1', {})

    await useMessageStore.getState().cancelStream(fakeClient, 'conv1')

    const state = useMessageStore.getState()
    expect(state.isStreaming).toBe(false)
    expect(state.messages.conv1 ?? []).toHaveLength(0)
    expect(cancelActiveRun).toHaveBeenCalledOnce()
  })

  it('is a no-op when not streaming the given conversation', async () => {
    await useMessageStore.getState().cancelStream(fakeClient, 'conv-other')
    expect(cancelActiveRun).not.toHaveBeenCalled()
  })
})
